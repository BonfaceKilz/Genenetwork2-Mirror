// Generated by CoffeeScript 1.7.1
var Scatter_Plot, root;

root = typeof exports !== "undefined" && exports !== null ? exports : this;

Scatter_Plot = (function() {
  function Scatter_Plot() {
    var chart, data, g, height, i, main, margin, maxx, maxy, minx, miny, sample1, sample2, samplename, samples_1, samples_2, text, width, x, xAxis, y, yAxis;
    data = new Array();
    samples_1 = js_data.samples_1;
    samples_2 = js_data.samples_2;
    i = 0;
    for (samplename in samples_1) {
      sample1 = samples_1[samplename];
      sample2 = samples_2[samplename];
      data[i++] = [sample1.value, sample2.value];
    }
    margin = {
      top: 100,
      right: 15,
      bottom: 60,
      left: 60
    };
    width = js_data.width - margin.left - margin.right;
    height = js_data.height - margin.top - margin.bottom;
    minx = d3.min(data, function(d) {
      return d[0];
    }) * 0.95;
    maxx = d3.max(data, function(d) {
      return d[0];
    }) * 1.05;
    miny = d3.min(data, function(d) {
      return d[1];
    }) * 0.95;
    maxy = d3.max(data, function(d) {
      return d[1];
    }) * 1.05;
    x = d3.scale.linear().domain([minx, maxx]).range([0, width]);
    y = d3.scale.linear().domain([miny, maxy]).range([height, 0]);
    chart = d3.select("#scatter_plot").append("svg:svg").attr("width", width + margin.right + margin.left).attr("height", height + margin.top + margin.bottom).attr("class", "chart");
    main = chart.append("g").attr("transform", "translate(" + margin.left + "," + margin.top + ")").attr("width", width).attr("height", height).attr("class", "main");
    xAxis = d3.svg.axis().scale(x).orient("bottom");
    main.append("g").attr("transform", "translate(0," + height + ")").attr("class", "main axis date").call(xAxis);
    yAxis = d3.svg.axis().scale(y).orient("left");
    main.append("g").attr("transform", "translate(0,0)").attr("class", "main axis date").call(yAxis);
    g = main.append("svg:g");
    g.selectAll("scatter-dots").data(data).enter().append("svg:circle").attr("cx", function(d) {
      return x(d[0]);
    }).attr("cy", function(d) {
      return y(d[1]);
    }).attr("fill", js_data.circle_color).attr("r", js_data.circle_radius);
    main.append("line").attr("x1", x(minx)).attr("y1", y(js_data.slope * minx + js_data.intercept)).attr("x2", x(maxx * 0.995)).attr("y2", y(js_data.slope * maxx * 0.995 + js_data.intercept)).style("stroke", js_data.line_color).style("stroke-width", js_data.line_width);
    chart.append("text").attr("x", width / 2).attr("y", margin.top / 2 - 25).text("Sample Correlation Scatterplot");
    text = "";
    text += "N=" + js_data.num_overlap;
    chart.append("text").attr("x", margin.left).attr("y", margin.top / 2 - 5).text(text);
    text = "";
    text += "r=" + js_data.r_value + "\t";
    text += "p(r)=" + js_data.p_value;
    chart.append("text").attr("x", margin.left).attr("y", margin.top / 2 + 15).text(text);
    text = "";
    text += "slope=" + js_data.slope + "\t";
    text += "intercept=" + js_data.intercept;
    chart.append("text").attr("x", margin.left).attr("y", margin.top / 2 + 35).text(text);
    chart.append("text").attr("x", width / 2).attr("y", height + margin.top + 35).text(js_data.trait_1);
    chart.append("text").attr("x", 20).attr("y", height / 2 + margin.top + 30).attr("transform", "rotate(-90 20," + (height / 2 + margin.top + 30) + ")").text(js_data.trait_2);
  }

  return Scatter_Plot;

})();

root.Scatter_Plot = Scatter_Plot;
