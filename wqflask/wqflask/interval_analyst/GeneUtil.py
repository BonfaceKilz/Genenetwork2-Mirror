import string

from wqflask.database import database_connection

# Just return a list of dictionaries
# each dictionary contains sub-dictionary
def loadGenes(chrName, diffCol, startMb, endMb, species='mouse'):
    fetchFields = ['SpeciesId', 'Id', 'GeneSymbol', 'GeneDescription', 'Chromosome', 'TxStart', 'TxEnd',
                   'Strand', 'GeneID', 'NM_ID', 'kgID', 'GenBankID', 'UnigenID', 'ProteinID', 'AlignID',
                   'exonCount', 'exonStarts', 'exonEnds', 'cdsStart', 'cdsEnd']

    # List All Species in the Gene Table
    speciesDict = {}
    results = []
    with database_connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT Species.Name, GeneList.SpeciesId "
                       "FROM Species, GeneList WHERE "
                       "GeneList.SpeciesId = Species.Id "
                       "GROUP BY GeneList.SpeciesId")
        results = cursor.fetchall()
        for item in results:
            speciesDict[item[0]] = item[1]

        # List current Species and other Species
        speciesId = speciesDict[species]
        otherSpecies = [[X, speciesDict[X]] for X in list(speciesDict.keys())]
        otherSpecies.remove([species, speciesId])
        cursor.execute(f"SELECT {', '.join(fetchFields)} FROM GeneList "
                       "WHERE SpeciesId = %s AND "
                       "Chromosome = %s AND "
                       "((TxStart > %s and TxStart <= %s) "
                       "OR (TxEnd > %s and TxEnd <= %s)) "
                       "ORDER BY txStart",
                       (speciesId, chrName,
                        startMb, endMb,
                        startMb, endMb))
        results = cursor.fetchall()

        GeneList = []
        if results:
            for result in results:
                newdict = {}
                for j, item in enumerate(fetchFields):
                    newdict[item] = result[j]
                # count SNPs if possible
                if diffCol and species == 'mouse':
                    cursor.execute(
                        "SELECT count(*) FROM BXDSnpPosition "
                        "WHERE Chr = '%s' AND "
                        "Mb >= %s AND Mb < %s "
                        "AND StrainId1 = %s AND StrainId2 = %s",
                    (chrName, f"{newdict['TxStart']:2.6f}",
                     f"{newdict['TxEnd']:2.6f}",
                     diffCol[0], diffCol[1],))
                    newdict["snpCount"] = cursor.fetchone()[0]
                    newdict["snpDensity"] = (
                        newdict["snpCount"] /
                        (newdict["TxEnd"] - newdict["TxStart"]) / 1000.0)
                else:
                    newdict["snpDensity"] = newdict["snpCount"] = 0
                try:
                    newdict['GeneLength'] = 1000.0 * \
                        (newdict['TxEnd'] - newdict['TxStart'])
                except:
                    pass
                # load gene from other Species by the same name
                for item in otherSpecies:
                    othSpec, othSpecId = item
                    newdict2 = {}
                    cursor.execute(
                        f"SELECT {', '.join(fetchFields)} FROM GeneList WHERE "
                        "SpeciesId = %s AND "
                        "geneSymbol= '%s' LIMIT 1",
                        (othSpecId,
                         newdict["GeneSymbol"]))
                    resultsOther = cursor.fetchone()
                    if resultsOther:
                        for j, item in enumerate(fetchFields):
                            newdict2[item] = resultsOther[j]

                        # count SNPs if possible, could be a separate function
                        if diffCol and othSpec == 'mouse':
                            cursor.execute(
                                "SELECT count(*) FROM BXDSnpPosition "
                                "WHERE Chr = %s AND Mb >= %s AND "
                                "Mb < %s AND StrainId1 = %s "
                                "AND StrainId2 = %s",
                                (chrName, f"{newdict['TxStart']:2.6f}",
                                 f"{newdict['TxEnd']:2.6f}",
                                 diffCol[0], diffCol[1]))
                            if snp_count := cursor.fetchone():
                                newdict2["snpCount"] = snp_count[0]

                            newdict2["snpDensity"] = (
                                newdict2["snpCount"]
                                / (newdict2["TxEnd"] - newdict2["TxStart"])
                                / 1000.0)
                        else:
                            newdict2["snpDensity"] = newdict2["snpCount"] = 0
                        try:
                            newdict2['GeneLength'] = (
                                1000.0 * (newdict2['TxEnd'] - newdict2['TxStart']))
                        except:
                            pass

                    newdict['%sGene' % othSpec] = newdict2

                GeneList.append(newdict)
        return GeneList
