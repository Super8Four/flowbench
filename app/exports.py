"""Typed XML preserves the nested JSON structure, including nulls and arrays."""

import base64
import re
from xml.etree import ElementTree as ET


def xml_export(data):
    def add(parent, value):
        if value is None:
            parent.set("type", "null")
        elif isinstance(value, bool):
            parent.set("type", "boolean")
            parent.text = str(value).lower()
        elif isinstance(value, dict):
            parent.set("type", "object")
            for key, child in value.items():
                add(ET.SubElement(parent, "field", name=key), child)
        elif isinstance(value, list):
            parent.set("type", "array")
            for child in value:
                add(ET.SubElement(parent, "item"), child)
        elif isinstance(value, (int, float)):
            parent.set("type", "number")
            parent.text = str(value)
        else:
            parent.set("type", "string")
            if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", value):
                parent.set("encoding", "base64")
                parent.text = base64.b64encode(value.encode("utf-8")).decode("ascii")
            else:
                parent.text = value

    root = ET.Element("iperf_run")
    add(root, data)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
