# Copyright (C) University of Tennessee Health Science Center, Memphis, TN.
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License
# as published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero General Public License for more details.
#
# This program is available from Source Forge: at GeneNetwork Project
# (sourceforge.net/projects/genenetwork/).
#
# Contact Drs. Robert W. Williams and Xiaodong Zhou (2010)
# at rwilliams@uthsc.edu and xzhou15@uthsc.edu
#
# This module is used by GeneNetwork project (www.genenetwork.org)
from dataclasses import dataclass
from dataclasses import field
from dataclasses import InitVar
from typing import Optional, Dict, List
from utility.tools import USE_REDIS, flat_file_exists, GN2_BASE_URL
from utility.db_tools import escape
from utility.db_tools import mescape
from utility.db_tools import create_in_clause
from maintenance import get_group_samplelists
from utility.tools import locate, locate_ignore_error, flat_files
from utility import gen_geno_ob
from utility import chunks
from utility import webqtlUtil
from db import webqtlDatabaseFunction
from base import species
from base import webqtlConfig
from base.webqtlConfig import TMPDIR
from urllib.parse import urlparse
from utility.tools import SQL_URI
from wqflask.database import database_connection
import os
import math
import collections
import codecs

import json
import requests
import pickle as pickle
import hashlib
from redis import Redis


r = Redis()

# Used by create_database to instantiate objects
# Each subclass will add to this
DS_NAME_MAP = {}


def create_dataset(dataset_name, dataset_type=None,
                   get_samplelist=True, group_name=None):
    if dataset_name == "Temp":
        dataset_type = "Temp"

    if not dataset_type:
        dataset_type = Dataset_Getter(dataset_name)

    dataset_ob = DS_NAME_MAP[dataset_type]
    dataset_class = globals()[dataset_ob]
    if dataset_type == "Temp":
        return dataset_class(dataset_name, get_samplelist, group_name)
    else:
        return dataset_class(dataset_name, get_samplelist)


@dataclass
class DatasetType:
    """Create a dictionary of samples where the value is set to Geno,
    Publish or ProbeSet. E.g.

        {'AD-cases-controls-MyersGeno': 'Geno',
         'AD-cases-controls-MyersPublish': 'Publish',
         'AKXDGeno': 'Geno',
         'AXBXAGeno': 'Geno',
         'AXBXAPublish': 'Publish',
         'Aging-Brain-UCIPublish': 'Publish',
         'All Phenotypes': 'Publish',
         'B139_K_1206_M': 'ProbeSet',
         'B139_K_1206_R': 'ProbeSet' ...
        }
        """
    redis_instance: InitVar[Redis]
    datasets: Optional[Dict] = field(init=False, default_factory=dict)
    data: Optional[Dict] = field(init=False)

    def __post_init__(self, redis_instance):
        self.redis_instance = redis_instance
        data = redis_instance.get("dataset_structure")
        if data:
            self.datasets = json.loads(data)
        else:
            # ZS: I don't think this should ever run unless Redis is
            # emptied
            try:
                data = json.loads(requests.get(
                    GN2_BASE_URL + "/api/v_pre1/gen_dropdown",
                    timeout=5).content)
                for _species in data['datasets']:
                    for group in data['datasets'][_species]:
                        for dataset_type in data['datasets'][_species][group]:
                            for dataset in data['datasets'][_species][group][dataset_type]:
                                short_dataset_name = dataset[1]
                                if dataset_type == "Phenotypes":
                                    new_type = "Publish"
                                elif dataset_type == "Genotypes":
                                    new_type = "Geno"
                                else:
                                    new_type = "ProbeSet"
                                self.datasets[short_dataset_name] = new_type
            except Exception:  # Do nothing
                pass

            self.redis_instance.set("dataset_structure",
                                    json.dumps(self.datasets))
        self.data = data

    def set_dataset_key(self, t, name):
        """If name is not in the object's dataset dictionary, set it, and
        update dataset_structure in Redis
        args:
          t: Type of dataset structure which can be: 'mrna_expr', 'pheno',
             'other_pheno', 'geno'
          name: The name of the key to inserted in the datasets dictionary

        """
        sql_query_mapping = {
            'mrna_expr': ("SELECT ProbeSetFreeze.Id FROM "
                          "ProbeSetFreeze WHERE "
                          "ProbeSetFreeze.Name = %s "),
            'pheno': ("SELECT InfoFiles.GN_AccesionId "
                      "FROM InfoFiles, PublishFreeze, InbredSet "
                      "WHERE InbredSet.Name = %s AND "
                      "PublishFreeze.InbredSetId = InbredSet.Id AND "
                      "InfoFiles.InfoPageName = PublishFreeze.Name"),
            'other_pheno': ("SELECT PublishFreeze.Name "
                            "FROM PublishFreeze, InbredSet "
                            "WHERE InbredSet.Name = %s AND "
                            "PublishFreeze.InbredSetId = InbredSet.Id"),
            'geno': ("SELECT GenoFreeze.Id FROM GenoFreeze WHERE "
                     "GenoFreeze.Name = %s ")
        }

        dataset_name_mapping = {
            "mrna_expr": "ProbeSet",
            "pheno": "Publish",
            "other_pheno": "Publish",
            "geno": "Geno",
        }

        group_name = name
        if t in ['pheno', 'other_pheno']:
            group_name = name.replace("Publish", "")

        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(sql_query_mapping[t], (group_name,))
            if cursor.fetchone():
                self.datasets[name] = dataset_name_mapping[t]
                self.redis_instance.set(
                    "dataset_structure", json.dumps(self.datasets))
                return True


    def __call__(self, name):
        if name not in self.datasets:
            for t in ["mrna_expr", "pheno", "other_pheno", "geno"]:
                # This has side-effects, with the end result being a
                # truth-y value
                if(self.set_dataset_key(t, name)):
                    break
        # Return None if name has not been set
        return self.datasets.get(name, None)


# Do the intensive work at startup one time only
Dataset_Getter = DatasetType(r)


def create_datasets_list():
    if USE_REDIS:
        key = "all_datasets"
        result = r.get(key)

        if result:
            datasets = pickle.loads(result)

    if result is None:
        datasets = list()
        type_dict = {'Publish': 'PublishFreeze',
                     'ProbeSet': 'ProbeSetFreeze',
                     'Geno': 'GenoFreeze'}

        for dataset_type in type_dict:
            with database_connection() as conn, conn.cursor() as cursor:
                cursor.execute("SELECT Name FROM %s",
                               (type_dict[dataset_type],))
                results = cursor.fetchall(query)
                if results:
                    for result in results:
                        datasets.append(
                            create_dataset(result.Name, dataset_type))
        if USE_REDIS:
            r.set(key, pickle.dumps(datasets, pickle.HIGHEST_PROTOCOL))
            r.expire(key, 60 * 60)

    return datasets


class Markers:
    """Todo: Build in cacheing so it saves us reading the same file more than once"""

    def __init__(self, name):
        json_data_fh = open(locate(name + ".json", 'genotype/json'))

        markers = []
        with open("%s/%s_snps.txt" % (flat_files('genotype/bimbam'), name), 'r') as bimbam_fh:
            if len(bimbam_fh.readline().split(", ")) > 2:
                delimiter = ", "
            elif len(bimbam_fh.readline().split(",")) > 2:
                delimiter = ","
            elif len(bimbam_fh.readline().split("\t")) > 2:
                delimiter = "\t"
            else:
                delimiter = " "
            for line in bimbam_fh:
                marker = {}
                marker['name'] = line.split(delimiter)[0].rstrip()
                marker['Mb'] = float(line.split(delimiter)[
                                     1].rstrip()) / 1000000
                marker['chr'] = line.split(delimiter)[2].rstrip()
                markers.append(marker)

        for marker in markers:
            if (marker['chr'] != "X") and (marker['chr'] != "Y") and (marker['chr'] != "M"):
                marker['chr'] = int(marker['chr'])
            marker['Mb'] = float(marker['Mb'])

        self.markers = markers

    def add_pvalues(self, p_values):
        if isinstance(p_values, list):
            # THIS IS only needed for the case when we are limiting the number of p-values calculated
            # if len(self.markers) > len(p_values):
            #    self.markers = self.markers[:len(p_values)]

            for marker, p_value in zip(self.markers, p_values):
                if not p_value:
                    continue
                marker['p_value'] = float(p_value)
                if math.isnan(marker['p_value']) or marker['p_value'] <= 0:
                    marker['lod_score'] = 0
                    marker['lrs_value'] = 0
                else:
                    marker['lod_score'] = -math.log10(marker['p_value'])
                    # Using -log(p) for the LRS; need to ask Rob how he wants to get LRS from p-values
                    marker['lrs_value'] = -math.log10(marker['p_value']) * 4.61
        elif isinstance(p_values, dict):
            filtered_markers = []
            for marker in self.markers:
                if marker['name'] in p_values:
                    marker['p_value'] = p_values[marker['name']]
                    if math.isnan(marker['p_value']) or (marker['p_value'] <= 0):
                        marker['lod_score'] = 0
                        marker['lrs_value'] = 0
                    else:
                        marker['lod_score'] = -math.log10(marker['p_value'])
                        # Using -log(p) for the LRS; need to ask Rob how he wants to get LRS from p-values
                        marker['lrs_value'] = - \
                            math.log10(marker['p_value']) * 4.61
                    filtered_markers.append(marker)
            self.markers = filtered_markers


class HumanMarkers(Markers):

    def __init__(self, name, specified_markers=[]):
        marker_data_fh = open(flat_files('mapping') + '/' + name + '.bim')
        self.markers = []
        for line in marker_data_fh:
            splat = line.strip().split()
            if len(specified_markers) > 0:
                if splat[1] in specified_markers:
                    marker = {}
                    marker['chr'] = int(splat[0])
                    marker['name'] = splat[1]
                    marker['Mb'] = float(splat[3]) / 1000000
                else:
                    continue
            else:
                marker = {}
                marker['chr'] = int(splat[0])
                marker['name'] = splat[1]
                marker['Mb'] = float(splat[3]) / 1000000
            self.markers.append(marker)

    def add_pvalues(self, p_values):
        super(HumanMarkers, self).add_pvalues(p_values)


class DatasetGroup:
    """
    Each group has multiple datasets; each species has multiple groups.

    For example, Mouse has multiple groups (BXD, BXA, etc), and each group
    has multiple datasets associated with it.

    """

    def __init__(self, dataset, name=None):
        """This sets self.group and self.group_id"""
        with database_connection() as conn, conn.cursor() as cursor:
            if not name:
                cursor.execute(dataset.query_for_group,
                               (dataset.name,))
            else:
                cursor.execute(
                    "SELECT InbredSet.Name, "
                    "InbredSet.Id, "
                    "InbredSet.GeneticType, "
                    "InbredSet.InbredSetCode "
                    "FROM InbredSet WHERE Name = %s",
                    (dataset.name,))
            (self.name, self.id,
             self.genetic_type, self.code) = cursor.fetchone()
        if self.name == 'BXD300':
            self.name = "BXD"

        self.f1list = None
        self.parlist = None
        self.get_f1_parent_strains()

        self.mapping_id, self.mapping_names = self.get_mapping_methods()

        self.species = webqtlDatabaseFunction.retrieve_species(self.name)

        self.incparentsf1 = False
        self.allsamples = None
        self._datasets = None
        self.genofile = None

    def get_mapping_methods(self):
        mapping_id = ()
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT MappingMethodId FROM "
                "InbredSet WHERE Name= %s",
                (self.name,))
            mapping_id = cursor.fetchone()[0]
        if mapping_id == "1":
            mapping_names = ["GEMMA", "QTLReaper", "R/qtl"]
        elif mapping_id == "2":
            mapping_names = ["GEMMA"]
        elif mapping_id == "3":
            mapping_names = ["R/qtl"]
        elif mapping_id == "4":
            mapping_names = ["GEMMA", "PLINK"]
        else:
            mapping_names = []

        return mapping_id, mapping_names

    def get_markers(self):
        def check_plink_gemma():
            if flat_file_exists("mapping"):
                MAPPING_PATH = flat_files("mapping") + "/"
                if os.path.isfile(MAPPING_PATH + self.name + ".bed"):
                    return True
            return False

        if check_plink_gemma():
            marker_class = HumanMarkers
        else:
            marker_class = Markers

        if self.genofile:
            self.markers = marker_class(self.genofile[:-5])
        else:
            self.markers = marker_class(self.name)

    def get_f1_parent_strains(self):
        try:
            # NL, 07/27/2010. ParInfo has been moved from webqtlForm.py to webqtlUtil.py;
            f1, f12, maternal, paternal = webqtlUtil.ParInfo[self.name]
        except KeyError:
            f1 = f12 = maternal = paternal = None

        if f1 and f12:
            self.f1list = [f1, f12]
        if maternal and paternal:
            self.parlist = [maternal, paternal]

    def get_study_samplelists(self):
        study_sample_file = locate_ignore_error(
            self.name + ".json", 'study_sample_lists')
        try:
            f = open(study_sample_file)
        except:
            return []
        study_samples = json.load(f)
        return study_samples

    def get_genofiles(self):
        jsonfile = "%s/%s.json" % (webqtlConfig.GENODIR, self.name)
        try:
            f = open(jsonfile)
        except:
            return None
        jsondata = json.load(f)
        return jsondata['genofile']

    def get_samplelist(self):
        result = None
        key = "samplelist:v3:" + self.name
        if USE_REDIS:
            result = r.get(key)

        if result is not None:
            self.samplelist = json.loads(result)
        else:
            genotype_fn = locate_ignore_error(self.name + ".geno", 'genotype')
            if genotype_fn:
                self.samplelist = get_group_samplelists.get_samplelist(
                    "geno", genotype_fn)
            else:
                self.samplelist = None

            if USE_REDIS:
                r.set(key, json.dumps(self.samplelist))
                r.expire(key, 60 * 5)

    def all_samples_ordered(self):
        result = []
        lists = (self.parlist, self.f1list, self.samplelist)
        [result.extend(l) for l in lists if l]
        return result

    def read_genotype_file(self, use_reaper=False):
        '''Read genotype from .geno file instead of database'''
        # genotype_1 is Dataset Object without parents and f1
        # genotype_2 is Dataset Object with parents and f1 (not for intercross)

        # reaper barfs on unicode filenames, so here we ensure it's a string
        if self.genofile:
            if "RData" in self.genofile:  # ZS: This is a temporary fix; I need to change the way the JSON files that point to multiple genotype files are structured to point to other file types like RData
                full_filename = str(
                    locate(self.genofile.split(".")[0] + ".geno", 'genotype'))
            else:
                full_filename = str(locate(self.genofile, 'genotype'))
        else:
            full_filename = str(locate(self.name + '.geno', 'genotype'))
        genotype_1 = gen_geno_ob.genotype(full_filename)

        if genotype_1.type == "group" and self.parlist:
            genotype_2 = genotype_1.add(
                Mat=self.parlist[0], Pat=self.parlist[1])  # , F1=_f1)
        else:
            genotype_2 = genotype_1

        # determine default genotype object
        if self.incparentsf1 and genotype_1.type != "intercross":
            genotype = genotype_2
        else:
            self.incparentsf1 = 0
            genotype = genotype_1

        self.samplelist = list(genotype.prgy)

        return genotype


def datasets(group_name, this_group=None):
    key = "group_dataset_menu:v2:" + group_name
    dataset_menu = []
    with database_connection() as conn, conn.cursor() as cursor:
        cursor.execute('''
            (SELECT '#PublishFreeze',PublishFreeze.FullName,PublishFreeze.Name
            FROM PublishFreeze,InbredSet
            WHERE PublishFreeze.InbredSetId = InbredSet.Id
                and InbredSet.Name = '%s'
            ORDER BY PublishFreeze.Id ASC)
            UNION
            (SELECT '#GenoFreeze',GenoFreeze.FullName,GenoFreeze.Name
            FROM GenoFreeze, InbredSet
            WHERE GenoFreeze.InbredSetId = InbredSet.Id
                and InbredSet.Name = '%s')
            UNION
            (SELECT Tissue.Name, ProbeSetFreeze.FullName,ProbeSetFreeze.Name
            FROM ProbeSetFreeze, ProbeFreeze, InbredSet, Tissue
            WHERE ProbeSetFreeze.ProbeFreezeId = ProbeFreeze.Id
                and ProbeFreeze.TissueId = Tissue.Id
                and ProbeFreeze.InbredSetId = InbredSet.Id
                and InbredSet.Name like %s
            ORDER BY Tissue.Name, ProbeSetFreeze.OrderList DESC)
            ''' % (group_name,
                group_name,
                "'" + group_name + "'"))
        the_results = cursor.fetchall()

    sorted_results = sorted(the_results, key=lambda kv: kv[0])

    # ZS: This is kind of awkward, but need to ensure Phenotypes show up before Genotypes in dropdown
    pheno_inserted = False
    geno_inserted = False
    for dataset_item in sorted_results:
        tissue_name = dataset_item[0]
        dataset = dataset_item[1]
        dataset_short = dataset_item[2]
        if tissue_name in ['#PublishFreeze', '#GenoFreeze']:
            if tissue_name == '#PublishFreeze' and (dataset_short == group_name + 'Publish'):
                dataset_menu.insert(
                    0, dict(tissue=None, datasets=[(dataset, dataset_short)]))
                pheno_inserted = True
            elif pheno_inserted and tissue_name == '#GenoFreeze':
                dataset_menu.insert(
                    1, dict(tissue=None, datasets=[(dataset, dataset_short)]))
                geno_inserted = True
            else:
                dataset_menu.append(
                    dict(tissue=None, datasets=[(dataset, dataset_short)]))
        else:
            tissue_already_exists = False
            for i, tissue_dict in enumerate(dataset_menu):
                if tissue_dict['tissue'] == tissue_name:
                    tissue_already_exists = True
                    break

            if tissue_already_exists:
                dataset_menu[i]['datasets'].append((dataset, dataset_short))
            else:
                dataset_menu.append(dict(tissue=tissue_name,
                                         datasets=[(dataset, dataset_short)]))

    if USE_REDIS:
        r.set(key, pickle.dumps(dataset_menu, pickle.HIGHEST_PROTOCOL))
        r.expire(key, 60 * 5)

    if this_group != None:
        this_group._datasets = dataset_menu
        return this_group._datasets
    else:
        return dataset_menu


class DataSet:
    """
    DataSet class defines a dataset in webqtl, can be either Microarray,
    Published phenotype, genotype, or user input dataset(temp)

    """

    def __init__(self, name, get_samplelist=True, group_name=None):

        assert name, "Need a name"
        self.name = name
        self.id = None
        self.shortname = None
        self.fullname = None
        self.type = None
        self.data_scale = None  # ZS: For example log2
        self.accession_id = None

        self.setup()

        if self.type == "Temp":  # Need to supply group name as input if temp trait
            # sets self.group and self.group_id and gets genotype
            self.group = DatasetGroup(self, name=group_name)
        else:
            self.check_confidentiality()
            self.retrieve_other_names()
            # sets self.group and self.group_id and gets genotype
            self.group = DatasetGroup(self)
            self.accession_id = self.get_accession_id()
        if get_samplelist == True:
            self.group.get_samplelist()
        self.species = species.TheSpecies(self)

    def as_dict(self):
        return {
            'name': self.name,
            'shortname': self.shortname,
            'fullname': self.fullname,
            'type': self.type,
            'data_scale': self.data_scale,
            'group': self.group.name,
            'accession_id': self.accession_id
        }

    def get_accession_id(self):
        results = None
        with database_connection() as conn, conn.cursor() as cursor:
            if self.type == "Publish":
                cursor.execute(
                    "SELECT InfoFiles.GN_AccesionId FROM "
                    "InfoFiles, PublishFreeze, InbredSet "
                    "WHERE InbredSet.Name = %s AND "
                    "PublishFreeze.InbredSetId = InbredSet.Id "
                    "AND InfoFiles.InfoPageName = PublishFreeze.Name "
                    "AND PublishFreeze.public > 0 AND "
                    "PublishFreeze.confidentiality < 1 "
                    "ORDER BY PublishFreeze.CreateTime DESC",
                    (self.group.name,)
                )
                results = cursor.fetchone()
            elif self.type == "Geno":
                cursor.execute(
                    "SELECT InfoFiles.GN_AccesionId FROM "
                    "InfoFiles, GenoFreeze, InbredSet "
                    "WHERE InbredSet.Name = %s AND "
                    "GenoFreeze.InbredSetId = InbredSet.Id "
                    "AND InfoFiles.InfoPageName = GenoFreeze.ShortName "
                    "AND GenoFreeze.public > 0 AND "
                    "GenoFreeze.confidentiality < 1 "
                    "ORDER BY GenoFreeze.CreateTime DESC",
                    (self.group.name,)
                )
                results = cursor.fetchone()

        if results:
            return str(results[0])
        return "None"

    def retrieve_other_names(self):
        """This method fetches the the dataset names in search_result.

        If the data set name parameter is not found in the 'Name' field of
        the data set table, check if it is actually the FullName or
        ShortName instead.

        This is not meant to retrieve the data set info if no name at
        all is passed.

        """
        with database_connection() as conn, conn.cursor() as cursor:
            try:
                if self.type == "ProbeSet":
                    cursor.execute(
                        "SELECT ProbeSetFreeze.Id, ProbeSetFreeze.Name, "
                        "ProbeSetFreeze.FullName, ProbeSetFreeze.ShortName, "
                        "ProbeSetFreeze.DataScale, Tissue.Name "
                        "FROM ProbeSetFreeze, ProbeFreeze, Tissue "
                        "WHERE ProbeSetFreeze.ProbeFreezeId = ProbeFreeze.Id "
                        "AND ProbeFreeze.TissueId = Tissue.Id "
                        "AND (ProbeSetFreeze.Name = %s OR "
                        "ProbeSetFreeze.FullName = %s "
                        "OR ProbeSetFreeze.ShortName = %s)",
                        (self.name,)*3)
                else:
                    self.tissue = "N/A"
                    cursor.execute(
                        "SELECT Id, Name, FullName, ShortName "
                        f"FROM {self.type}Freeze "
                        "WHERE (Name = %s OR FullName = "
                        "%s OR ShortName = %s)",
                        (self.name,)*3)
                (self.id, self.name, self.fullname, self.shortname,
                 self.data_scale, self.tissue) = cursor.fetchone()
            except TypeError:
                pass

    def chunk_dataset(self, dataset, n):

        results = {}
        traits_name_dict = ()
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT ProbeSetXRef.DataId,ProbeSet.Name "
                "FROM ProbeSet, ProbeSetXRef, ProbeSetFreeze "
                "WHERE ProbeSetFreeze.Name = %s AND "
                "ProbeSetXRef.ProbeSetFreezeId = ProbeSetFreeze.Id "
                "AND ProbeSetXRef.ProbeSetId = ProbeSet.Id",
                (self.name,))
            # should cache this
            traits_name_dict = dict(cursor.fetchall())

        for i in range(0, len(dataset), n):
            matrix = list(dataset[i:i + n])
            trait_name = traits_name_dict[matrix[0][0]]

            my_values = [value for (trait_name, strain, value) in matrix]
            results[trait_name] = my_values
        return results

    def get_probeset_data(self, sample_list=None, trait_ids=None):

        # improvement of get trait data--->>>
        if sample_list:
            self.samplelist = sample_list

        else:
            self.samplelist = self.group.samplelist

        if self.group.parlist != None and self.group.f1list != None:
            if (self.group.parlist + self.group.f1list) in self.samplelist:
                self.samplelist += self.group.parlist + self.group.f1list
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT Strain.Name, Strain.Id FROM "
                "Strain, Species WHERE Strain.Name IN "
                f"{create_in_clause(self.samplelist)} "
                "AND Strain.SpeciesId=Species.Id AND "
                "Species.name = %s", (self.group.species,)
            )
            results = dict(cursor.fetchall())
            sample_ids = [results[item] for item in self.samplelist]

            sorted_samplelist = [strain_name for strain_name, strain_id in sorted(
                results.items(), key=lambda item: item[1])]

            cursor.execute(
                "SELECT * from ProbeSetData WHERE StrainID IN "
                f"{create_in_clause(sample_ids)} AND id IN "
                "(SELECT ProbeSetXRef.DataId FROM "
                "(ProbeSet, ProbeSetXRef, ProbeSetFreeze) "
                "WHERE ProbeSetXRef.ProbeSetFreezeId = ProbeSetFreeze.Id "
                "AND ProbeSetFreeze.Name = %s AND "
                "ProbeSet.Id = ProbeSetXRef.ProbeSetId)",
                (self.name,)
            )

            query_results = list(cursor.fetchall())
            data_results = self.chunk_dataset(query_results, len(sample_ids))
            self.samplelist = sorted_samplelist
            self.trait_data = data_results

    def get_trait_data(self, sample_list=None):
        if sample_list:
            self.samplelist = sample_list
        else:
            self.samplelist = self.group.samplelist

        if self.group.parlist != None and self.group.f1list != None:
            if (self.group.parlist + self.group.f1list) in self.samplelist:
                self.samplelist += self.group.parlist + self.group.f1list

        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT Strain.Name, Strain.Id FROM Strain, Species "
                f"WHERE Strain.Name IN {create_in_clause(self.samplelist)} "
                "AND Strain.SpeciesId=Species.Id "
                "AND Species.name = %s",
                (self.group.species,)
            )
            results = dict(cursor.fetchall())
            sample_ids = [
                sample_id for sample_id in
                (results.get(item) for item in self.samplelist
                 if item is not None)
                if sample_id is not None
            ]

            # MySQL limits the number of tables that can be used in a join to 61,
            # so we break the sample ids into smaller chunks
            # Postgres doesn't have that limit, so we can get rid of this after we transition
            chunk_size = 50
            number_chunks = int(math.ceil(len(sample_ids) / chunk_size))

            cached_results = fetch_cached_results(self.name, self.type, self.samplelist)

            if cached_results is None:
                trait_sample_data = []
                for sample_ids_step in chunks.divide_into_chunks(sample_ids, number_chunks):
                    if self.type == "Publish":
                        dataset_type = "Phenotype"
                    else:
                        dataset_type = self.type
                    temp = ['T%s.value' % item for item in sample_ids_step]
                    if self.type == "Publish":
                        query = "SELECT {}XRef.Id,".format(escape(self.type))
                    else:
                        query = "SELECT {}.Name,".format(escape(dataset_type))
                    data_start_pos = 1
                    query += ', '.join(temp)
                    query += ' FROM ({}, {}XRef, {}Freeze) '.format(*mescape(dataset_type,
                                                                             self.type,
                                                                             self.type))

                    for item in sample_ids_step:
                        query += """
                                left join {}Data as T{} on T{}.Id = {}XRef.DataId
                                and T{}.StrainId={}\n
                                """.format(*mescape(self.type, item, item, self.type, item, item))

                    if self.type == "Publish":
                        query += """
                                WHERE {}XRef.InbredSetId = {}Freeze.InbredSetId
                                and {}Freeze.Name = '{}'
                                and {}.Id = {}XRef.{}Id
                                order by {}.Id
                                """.format(*mescape(self.type, self.type, self.type, self.name,
                                                    dataset_type, self.type, dataset_type, dataset_type))
                    else:
                        query += """
                                WHERE {}XRef.{}FreezeId = {}Freeze.Id
                                and {}Freeze.Name = '{}'
                                and {}.Id = {}XRef.{}Id
                                order by {}.Id
                                """.format(*mescape(self.type, self.type, self.type, self.type,
                                                    self.name, dataset_type, self.type, self.type, dataset_type))
                    cursor.execute(query)
                    results = cursor.fetchall()
                    trait_sample_data.append([list(result) for result in results])

                trait_count = len(trait_sample_data[0])
                self.trait_data = collections.defaultdict(list)

                data_start_pos = 1
                for trait_counter in range(trait_count):
                    trait_name = trait_sample_data[0][trait_counter][0]
                    for chunk_counter in range(int(number_chunks)):
                        self.trait_data[trait_name] += (
                            trait_sample_data[chunk_counter][trait_counter][data_start_pos:])

                cache_dataset_results(
                    self.name, self.type, self.samplelist, self.trait_data)
            else:
                self.trait_data = cached_results


class PhenotypeDataSet(DataSet):
    DS_NAME_MAP['Publish'] = 'PhenotypeDataSet'

    def setup(self):
        # Fields in the database table
        self.search_fields = ['Phenotype.Post_publication_description',
                              'Phenotype.Pre_publication_description',
                              'Phenotype.Pre_publication_abbreviation',
                              'Phenotype.Post_publication_abbreviation',
                              'PublishXRef.mean',
                              'Phenotype.Lab_code',
                              'Publication.PubMed_ID',
                              'Publication.Abstract',
                              'Publication.Title',
                              'Publication.Authors',
                              'PublishXRef.Id']

        # Figure out what display_fields is
        self.display_fields = ['name', 'group_code',
                               'pubmed_id',
                               'pre_publication_description',
                               'post_publication_description',
                               'original_description',
                               'pre_publication_abbreviation',
                               'post_publication_abbreviation',
                               'mean',
                               'lab_code',
                               'submitter', 'owner',
                               'authorized_users',
                               'authors', 'title',
                               'abstract', 'journal',
                               'volume', 'pages',
                               'month', 'year',
                               'sequence', 'units', 'comments']

        # Fields displayed in the search results table header
        self.header_fields = ['Index',
                              'Record',
                              'Description',
                              'Authors',
                              'Year',
                              'Max LRS',
                              'Max LRS Location',
                              'Additive Effect']

        self.type = 'Publish'
        self.query_for_group = """
SELECT InbredSet.Name, InbredSet.Id, InbredSet.GeneticType, InbredSet.InbredSetCode FROM InbredSet, PublishFreeze WHERE PublishFreeze.InbredSetId = InbredSet.Id AND PublishFreeze.Name = %s"""

    def check_confidentiality(self):
        # (Urgently?) Need to write this
        pass

    def get_trait_info(self, trait_list, species=''):
        for this_trait in trait_list:

            if not this_trait.haveinfo:
                this_trait.retrieve_info(get_qtl_info=True)

            description = this_trait.post_publication_description

            # If the dataset is confidential and the user has access to confidential
            # phenotype traits, then display the pre-publication description instead
            # of the post-publication description
            if this_trait.confidential:
                this_trait.description_display = ""
                continue   # for now, because no authorization features

                if not webqtlUtil.hasAccessToConfidentialPhenotypeTrait(
                        privilege=self.privilege,
                        userName=self.userName,
                        authorized_users=this_trait.authorized_users):

                    description = this_trait.pre_publication_description

            if len(description) > 0:
                this_trait.description_display = description.strip()
            else:
                this_trait.description_display = ""

            if not this_trait.year.isdigit():
                this_trait.pubmed_text = "N/A"
            else:
                this_trait.pubmed_text = this_trait.year

            if this_trait.pubmed_id:
                this_trait.pubmed_link = webqtlConfig.PUBMEDLINK_URL % this_trait.pubmed_id

            # LRS and its location
            this_trait.LRS_score_repr = "N/A"
            this_trait.LRS_location_repr = "N/A"

            if this_trait.lrs:
                with database_connection() as conn, conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT Geno.Chr, Geno.Mb FROM "
                        "Geno, Species WHERE "
                        "Species.Name = %s AND "
                        "Geno.Name = %s AND "
                        "Geno.SpeciesId = Species.Id",
                        (species, this_trait.locus,)
                    )
                    if result := cursor.fetchone():
                        if result[0] and result[1]:
                            LRS_Chr, LRS_Mb = result[0], result[1]
                            this_trait.LRS_score_repr = LRS_score_repr = '%3.1f' % this_trait.lrs
                            this_trait.LRS_location_repr = LRS_location_repr = 'Chr%s: %.6f' % (
                                LRS_Chr, float(LRS_Mb))

    def retrieve_sample_data(self, trait):
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
            "SELECT Strain.Name, PublishData.value, "
                "PublishSE.error, NStrain.count, "
                "Strain.Name2 FROM (PublishData, Strain, "
                "PublishXRef, PublishFreeze) LEFT JOIN "
                "PublishSE ON "
                "(PublishSE.DataId = PublishData.Id "
                "AND PublishSE.StrainId = PublishData.StrainId) "
                "LEFT JOIN NStrain ON "
                "(NStrain.DataId = PublishData.Id AND "
                "NStrain.StrainId = PublishData.StrainId) "
                "WHERE PublishXRef.InbredSetId = PublishFreeze.InbredSetId "
                "AND PublishData.Id = PublishXRef.DataId AND "
                "PublishXRef.Id = %s AND PublishFreeze.Id = %s "
                "AND PublishData.StrainId = Strain.Id "
                "ORDER BY Strain.Name", (trait, self.id))
            return cursor.fetchall()


class GenotypeDataSet(DataSet):
    DS_NAME_MAP['Geno'] = 'GenotypeDataSet'

    def setup(self):
        # Fields in the database table
        self.search_fields = ['Name',
                              'Chr']

        # Find out what display_fields is
        self.display_fields = ['name',
                               'chr',
                               'mb',
                               'source2',
                               'sequence']

        # Fields displayed in the search results table header
        self.header_fields = ['Index',
                              'ID',
                              'Location']

        # Todo: Obsolete or rename this field
        self.type = 'Geno'
        self.query_for_group = """
SELECT InbredSet.Name, InbredSet.Id, InbredSet.GeneticType, InbredSet.InbredSetCode
FROM InbredSet, GenoFreeze WHERE GenoFreeze.InbredSetId = InbredSet.Id AND
GenoFreeze.Name = %s"""

    def check_confidentiality(self):
        return geno_mrna_confidentiality(self)

    def get_trait_info(self, trait_list, species=None):
        for this_trait in trait_list:
            if not this_trait.haveinfo:
                this_trait.retrieveInfo()

            if this_trait.chr and this_trait.mb:
                this_trait.location_repr = 'Chr%s: %.6f' % (
                    this_trait.chr, float(this_trait.mb))

    def retrieve_sample_data(self, trait):
        results = []
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT Strain.Name, GenoData.value, "
                "GenoSE.error, 'N/A', Strain.Name2 "
                "FROM (GenoData, GenoFreeze, Strain, Geno, "
                "GenoXRef) LEFT JOIN GenoSE ON "
                "(GenoSE.DataId = GenoData.Id AND "
                "GenoSE.StrainId = GenoData.StrainId) "
                "WHERE Geno.SpeciesId = %s AND "
                "Geno.Name = %s AND GenoXRef.GenoId = Geno.Id "
                "AND GenoXRef.GenoFreezeId = GenoFreeze.Id "
                "AND GenoFreeze.Name = %s AND "
                "GenoXRef.DataId = GenoData.Id "
                "AND GenoData.StrainId = Strain.Id "
                "ORDER BY Strain.Name",
                (webqtlDatabaseFunction.retrieve_species_id(self.group.name),
                 trait, self.name,))
            results = list(cursor.fetchall())

        if self.group.name in webqtlUtil.ParInfo:
            f1_1, f1_2, ref, nonref = webqtlUtil.ParInfo[self.group.name]
            results.append([f1_1, 0, None, "N/A", f1_1])
            results.append([f1_2, 0, None, "N/A", f1_2])
            results.append([ref, -1, None, "N/A", ref])
            results.append([nonref, 1, None, "N/A", nonref])

        return results


class MrnaAssayDataSet(DataSet):
    '''
    An mRNA Assay is a quantitative assessment (assay) associated with an mRNA trait

    This used to be called ProbeSet, but that term only refers specifically to the Affymetrix
    platform and is far too specific.

    '''
    DS_NAME_MAP['ProbeSet'] = 'MrnaAssayDataSet'

    def setup(self):
        # Fields in the database table
        self.search_fields = ['Name',
                              'Description',
                              'Probe_Target_Description',
                              'Symbol',
                              'Alias',
                              'GenbankId',
                              'UniGeneId',
                              'RefSeq_TranscriptId']

        # Find out what display_fields is
        self.display_fields = ['name', 'symbol',
                               'description', 'probe_target_description',
                               'chr', 'mb',
                               'alias', 'geneid',
                               'genbankid', 'unigeneid',
                               'omim', 'refseq_transcriptid',
                               'blatseq', 'targetseq',
                               'chipid', 'comments',
                               'strand_probe', 'strand_gene',
                               'proteinid', 'uniprotid',
                               'probe_set_target_region',
                               'probe_set_specificity',
                               'probe_set_blat_score',
                               'probe_set_blat_mb_start',
                               'probe_set_blat_mb_end',
                               'probe_set_strand',
                               'probe_set_note_by_rw',
                               'flag']

        # Fields displayed in the search results table header
        self.header_fields = ['Index',
                              'Record',
                              'Symbol',
                              'Description',
                              'Location',
                              'Mean',
                              'Max LRS',
                              'Max LRS Location',
                              'Additive Effect']

        # Todo: Obsolete or rename this field
        self.type = 'ProbeSet'
        self.query_for_group = """
SELECT InbredSet.Name, InbredSet.Id, InbredSet.GeneticType, InbredSet.InbredSetCode
FROM InbredSet, ProbeSetFreeze, ProbeFreeze WHERE ProbeFreeze.InbredSetId = InbredSet.Id AND
ProbeFreeze.Id = ProbeSetFreeze.ProbeFreezeId AND ProbeSetFreeze.Name = %s"""

    def check_confidentiality(self):
        return geno_mrna_confidentiality(self)

    def get_trait_info(self, trait_list=None, species=''):

        #  Note: setting trait_list to [] is probably not a great idea.
        if not trait_list:
            trait_list = []
        with database_connection() as conn, conn.cursor() as cursor:
            for this_trait in trait_list:

                if not this_trait.haveinfo:
                    this_trait.retrieveInfo(QTL=1)

                if not this_trait.symbol:
                    this_trait.symbol = "N/A"

                # XZ, 12/08/2008: description
                # XZ, 06/05/2009: Rob asked to add probe target description
                description_string = str(
                    str(this_trait.description).strip(codecs.BOM_UTF8), 'utf-8')
                target_string = str(
                    str(this_trait.probe_target_description).strip(codecs.BOM_UTF8), 'utf-8')

                if len(description_string) > 1 and description_string != 'None':
                    description_display = description_string
                else:
                    description_display = this_trait.symbol

                if (len(description_display) > 1 and description_display != 'N/A'
                        and len(target_string) > 1 and target_string != 'None'):
                    description_display = description_display + '; ' + target_string.strip()

                # Save it for the jinja2 template
                this_trait.description_display = description_display

                if this_trait.chr and this_trait.mb:
                    this_trait.location_repr = 'Chr%s: %.6f' % (
                        this_trait.chr, float(this_trait.mb))

                # Get mean expression value
                cursor.execute(
                    "SELECT ProbeSetXRef.mean FROM "
                    "ProbeSetXRef, ProbeSet WHERE "
                    "ProbeSetXRef.ProbeSetFreezeId = %s "
                    "AND ProbeSet.Id = ProbeSetXRef.ProbeSetId "
                    "AND ProbeSet.Name = %s",
                    (str(this_trait.dataset.id), this_trait.name,)
                )
                result = cursor.fetchone()

                mean = result[0] if result else 0

                if mean:
                    this_trait.mean = "%2.3f" % mean

                # LRS and its location
                this_trait.LRS_score_repr = 'N/A'
                this_trait.LRS_location_repr = 'N/A'

                # Max LRS and its Locus location
                if this_trait.lrs and this_trait.locus:
                    cursor.execute(
                        "SELECT Geno.Chr, Geno.Mb FROM "
                        "Geno, Species WHERE "
                        "Species.Name = %s AND "
                        "Geno.Name = %s AND "
                        "Geno.SpeciesId = Species.Id",
                        (species, this_trait.locus,)
                    )
                    if result := cursor.fetchone():
                        lrs_chr, lrs_mb = result
                        this_trait.LRS_score_repr = '%3.1f' % this_trait.lrs
                        this_trait.LRS_location_repr = 'Chr%s: %.6f' % (
                            lrs_chr, float(lrs_mb))

        return trait_list

    def retrieve_sample_data(self, trait):
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT Strain.Name, ProbeSetData.value, "
                "ProbeSetSE.error, NStrain.count, "
                "Strain.Name2 FROM (ProbeSetData, "
                "ProbeSetFreeze, Strain, ProbeSet, "
                "ProbeSetXRef) LEFT JOIN ProbeSetSE ON "
                "(ProbeSetSE.DataId = ProbeSetData.Id AND "
                "ProbeSetSE.StrainId = ProbeSetData.StrainId) "
                "LEFT JOIN NStrain ON "
                "(NStrain.DataId = ProbeSetData.Id AND "
                "NStrain.StrainId = ProbeSetData.StrainId) "
                "WHERE ProbeSet.Name = %s AND "
                "ProbeSetXRef.ProbeSetId = ProbeSet.Id "
                "AND ProbeSetXRef.ProbeSetFreezeId = ProbeSetFreeze.Id "
                "AND ProbeSetFreeze.Name = %s AND "
                "ProbeSetXRef.DataId = ProbeSetData.Id "
                "AND ProbeSetData.StrainId = Strain.Id "
                "ORDER BY Strain.Name",
                (trait, self.name,)
            )
            return cursor.fetchall()

    def retrieve_genes(self, column_name):
        with database_connection() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"SELECT ProbeSet.Name, ProbeSet.{column_name} "
                "FROM ProbeSet,ProbeSetXRef WHERE "
                "ProbeSetXRef.ProbeSetFreezeId = %s "
                "AND ProbeSetXRef.ProbeSetId=ProbeSet.Id",
                (str(self.id),))
            return dict(cursor.fetchall())


class TempDataSet(DataSet):
    """Temporary user-generated data set"""
    DS_NAME_MAP['Temp'] = 'TempDataSet'

    def setup(self):
        self.search_fields = ['name',
                              'description']

        self.display_fields = ['name',
                               'description']

        self.header_fields = ['Name',
                              'Description']

        self.type = 'Temp'

        # Need to double check later how these are used
        self.id = 1
        self.fullname = 'Temporary Storage'
        self.shortname = 'Temp'


def geno_mrna_confidentiality(ob):
    with database_connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT confidentiality, "
            f"AuthorisedUsers FROM {ob.type}Freeze WHERE Name = %s",
            (ob.name,)
        )
        result = cursor.fetchall()[0]
        if result:
            return True


def parse_db_url():
    parsed_db = urlparse(SQL_URI)

    return (parsed_db.hostname, parsed_db.username,
            parsed_db.password, parsed_db.path[1:])


def query_table_timestamp(dataset_type: str):
    """function to query the update timestamp of a given dataset_type"""

    # computation data and actions
    with database_connection() as conn, conn.cursor() as cursor:
        fetch_db_name = parse_db_url()
        cursor.execute(
            "SELECT UPDATE_TIME FROM "
            "information_schema.tables "
            f"WHERE TABLE_SCHEMA = '{fetch_db_name[-1]}' "
            f"AND TABLE_NAME = '{dataset_type}Data'")
        date_time_obj = cursor.fetchone()[0]
        return date_time_obj.strftime("%Y-%m-%d %H:%M:%S")


def generate_hash_file(dataset_name: str, dataset_type: str, dataset_timestamp: str, samplelist: str):
    """given the trait_name generate a unique name for this"""
    string_unicode = f"{dataset_name}{dataset_timestamp}{samplelist}".encode()
    md5hash = hashlib.md5(string_unicode)
    return md5hash.hexdigest()


def cache_dataset_results(dataset_name: str, dataset_type: str, samplelist: List, query_results: List):
    """function to cache dataset query results to file
    input dataset_name and type query_results(already processed in default dict format)
    """
    # data computations actions
    # store the file path on redis

    table_timestamp = query_table_timestamp(dataset_type)
    samplelist_as_str = ",".join(samplelist)

    file_name = generate_hash_file(dataset_name, dataset_type, table_timestamp, samplelist_as_str)
    file_path = os.path.join(TMPDIR, f"{file_name}.json")

    with open(file_path, "w") as file_handler:
        json.dump(query_results, file_handler)


def fetch_cached_results(dataset_name: str, dataset_type: str, samplelist: List):
    """function to fetch the cached results"""

    table_timestamp = query_table_timestamp(dataset_type)
    samplelist_as_str = ",".join(samplelist)

    file_name = generate_hash_file(dataset_name, dataset_type, table_timestamp, samplelist_as_str)
    file_path = os.path.join(TMPDIR, f"{file_name}.json")
    try:
        with open(file_path, "r") as file_handler:

            return json.load(file_handler)

    except Exception:
        pass
