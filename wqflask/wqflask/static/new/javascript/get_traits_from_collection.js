// Generated by CoffeeScript 1.6.1
(function() {
  var back_to_collections, collection_click, collection_list, color_by_trait, process_traits, trait_click;

  console.log("before get_traits_from_collection");

  collection_list = null;

  collection_click = function() {
    var this_collection_url;
    console.log("Clicking on:", $(this));
    this_collection_url = $(this).find('.collection_name').prop("href");
    this_collection_url += "&json";
    console.log("this_collection_url", this_collection_url);
    collection_list = $("#collections_holder").html();
    return $.ajax({
      dataType: "json",
      url: this_collection_url,
      success: process_traits
    });
  };

  trait_click = function() {
    var dataset, this_trait_url, trait;
    console.log("Clicking on:", $(this));
    trait = $(this).find('.trait').text();
    dataset = $(this).find('.dataset').text();
    this_trait_url = "/trait/get_sample_data?trait=" + trait + "&dataset=" + dataset;
    console.log("this_trait_url", this_trait_url);
    return $.ajax({
      dataType: "json",
      url: this_trait_url,
      success: color_by_trait
    });
  };

  color_by_trait = function(trait_sample_data, textStatus, jqXHR) {
    return console.log('in color_by_trait:', trait_sample_data);
  };

  process_traits = function(trait_data, textStatus, jqXHR) {
    var the_html, trait, _i, _len;
    console.log('in process_traits with trait_data:', trait_data);
    the_html = "<button id='back_to_collections' class='btn btn-inverse btn-small'>";
    the_html += "<i class='icon-white icon-arrow-left'></i> Back </button>";
    the_html += "<table class='table table-hover'>";
    the_html += "<thead><tr><th>Record</th><th>Data Set</th><th>Description</th><th>Mean</th></tr></thead>";
    the_html += "<tbody>";
    for (_i = 0, _len = trait_data.length; _i < _len; _i++) {
      trait = trait_data[_i];
      the_html += "<tr class='trait_line'><td class='trait'>" + trait.name + "</td>";
      the_html += "<td class='dataset'>" + trait.dataset + "</td>";
      the_html += "<td>" + trait.description + "</td>";
      the_html += "<td>" + (trait.mean || '&nbsp;') + "</td></tr>";
    }
    the_html += "</tbody>";
    the_html += "</table>";
    $("#collections_holder").html(the_html);
    return $('#collections_holder').colorbox.resize();
  };

  back_to_collections = function() {
    console.log("collection_list:", collection_list);
    $("#collections_holder").html(collection_list);
    $(document).on("click", ".collection_line", collection_click);
    return $('#collections_holder').colorbox.resize();
  };

  $(function() {
    console.log("inside get_traits_from_collection");
    $(document).on("click", ".collection_line", collection_click);
    $(document).on("click", ".trait_line", trait_click);
    return $(document).on("click", "#back_to_collections", back_to_collections);
  });

}).call(this);
