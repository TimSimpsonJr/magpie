# tests/conftest_citation.py -- ASCII only. Synthetic DoclingDocument dicts.
def make_block(index, text, page_no=1, *, bbox=None, charspan=None, prov=None):
    """One texts[] item. self_ref == '#/texts/{index}'. Single-prov by default
    (charspan defaults to [0, len(text)) -- but pass charspan/prov to model the
    real-world cases where prov.charspan != [0,len) or n_prov > 1)."""
    if bbox is None:
        bbox = {"l": 72.0, "t": 700.0, "r": 540.0, "b": 688.0,
                "coord_origin": "BOTTOMLEFT"}
    if prov is None:
        cs = charspan if charspan is not None else [0, len(text)]
        prov = [{"page_no": page_no, "bbox": bbox, "charspan": cs}]
    return {"self_ref": f"#/texts/{index}", "parent": {"$ref": "#/body"},
            "children": [], "label": "text", "prov": prov,
            "orig": text, "text": text}


def make_doc(blocks, *, pages=None, schema_version="1.10.0"):
    """A minimal DoclingDocument dict: top-level texts[] + pages + schema."""
    if pages is None:
        pages = {"1": {"size": {"width": 612.0, "height": 792.0}, "page_no": 1}}
    return {"schema_name": "DoclingDocument", "version": schema_version,
            "texts": list(blocks), "tables": [], "pictures": [], "groups": [],
            "body": {"self_ref": "#/body", "children": [], "label": "unspecified"},
            "form_items": [], "key_value_items": [], "furniture": {}, "pages": pages,
            "name": "synthetic"}
