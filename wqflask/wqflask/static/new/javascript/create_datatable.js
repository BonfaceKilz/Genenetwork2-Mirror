create_table = function(tableId, tableData, columnDefs, customSettings) {

    loadDataTable(tableId=tableId, tableFata=tableData, tableSettings=tableSettings, firstRun=true)

    function loadDataTable(tableId, tableData, tableSettings, firstRun=false){
        if (!firstRun){
            setUserColumnsDefWidths(tableId);
        }

        tableSettings = {
            'data': tableData,
            'columns': columnDefs,
            "sDom": "iti",
            "destroy": true,
            "autoWidth": false,
            "bSortClasses": false,
            "scrollY": "100vh",
            "scrollCollapse": true,
            "scroller":  true,
            "iDisplayLength": -1,
            "initComplete": function (settings) {
                // Add JQueryUI resizable functionality to each th in the ScrollHead table
                $('#' + table_id + '_wrapper .dataTables_scrollHead thead th').resizable({
                    handles: "e",
                    alsoResize: '#' + tableId + '_wrapper .dataTables_scrollHead table', //Not essential but makes the resizing smoother
                    resize: function( event, ui ) {
                        widthChange = ui.size.width - ui.originalSize.width;
                    },
                    stop: function () {
                        saveColumnSettings(table_id, the_table);
                        loadDataTable(first_run=false, table_id, table_data);
                    }
                });
            }
        }

        // Replace default settings with custom settings or add custom settings if not already set in default settings
        $.each(customSettings, function(key, value) {
            tableSettings[key] = value
        });
    }

    if (!firstRun){
        $('#' + tableId + '_container').css("width", String($('#' + tableId).width() + widthChange + 17) + "px"); // Change the container width by the change in width of the adjusted column, so the overall table size adjusts properly

        let checkedRows = getCheckedRows(tableId);
        theTable = $('#' + tableId).DataTable(tableSettings);
        if (checkedRows.length > 0){
            recheckRows(theTable, checkedRows);
        }
    } else {
        theTable = $('#' + tableId).DataTable(tableSettings);
        theTable.draw();
    }

    theTable.on( 'order.dt search.dt draw.dt', function () {
        theTable.column(1, {search:'applied', order:'applied'}).nodes().each( function (cell, i) {
        cell.innerHTML = i+1;
        } );
    } ).draw();

    if (firstRun){
        $('#' + tableId + '_container').css("width", String($('#' + tableId).width() + 17) + "px");
    }

    $('#' + tableId + '_searchbox').on( 'keyup', function () {
        theTable.search($(this).val()).draw();
    } );

    $('.toggle-vis').on('click', function (e) {
        e.preventDefault();

        function toggleColumn(column) {
            // Toggle column visibility
            column.visible( ! column.visible() );
            if (column.visible()){
                $(this).removeClass("active");
            } else {
                $(this).addClass("active");
            }
        }

        // Get the column API object
        var targetCols = $(this).attr('data-column').split(",")
        for (let i = 0; i < targetCols.length; i++){
            var column = theTable.column( target_cols[i] );
            toggleColumn(column);
        }
    } );
}
