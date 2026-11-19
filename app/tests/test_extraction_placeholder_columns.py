from app.modules.extraction.parser import (
    _absorb_placeholder_cells,
    _map_raw_row_cells,
)


def test_bom_value_under_placeholder_moves_to_blank_labelled_neighbour():
    # Shape seen on 'BRP Mock 56043-021.pdf': the BOM value sits under Col3,
    # the repaired 'EVCO MFG #' label one slot to the right is blank.
    cells = {
        "Mold": "1428", "EVCO PN": "9415018", "Col3": "1428/9415018", "EVCO MFG #": "",
        "Col5": "", "Customer PN": "321886", "Part Description": "Elbow",
        "Parts/ Box": "2,000", "MOQ": "2,000", "Price Each": "$1.06",
    }

    mapped = _map_raw_row_cells(cells)

    assert mapped["manufacturing_bom_number"] == "1428/9415018"
    assert mapped["evco_part_number"] == "9415018"


def test_placeholder_value_is_left_alone_when_both_neighbours_are_blank():
    cells = {"EVCO PN": "", "Col3": "1428/9415018", "EVCO MFG #": ""}

    assert _absorb_placeholder_cells(cells) == cells


def test_placeholder_value_is_left_alone_when_both_neighbours_are_filled():
    cells = {"EVCO PN": "9415018", "Col3": "stray", "EVCO MFG #": "1428/9415018"}

    assert _absorb_placeholder_cells(cells) == cells


def test_filled_labelled_cells_are_never_overwritten():
    cells = {"EVCO PN": "9415018", "Col3": "1428/9415018", "EVCO MFG #": "", "Col5": "other"}

    out = _absorb_placeholder_cells(cells)

    assert out["EVCO PN"] == "9415018"
    assert out["EVCO MFG #"] == "1428/9415018"
    assert out["Col5"] == "other"  # its only labelled neighbour is now filled
