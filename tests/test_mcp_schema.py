import json
import unittest
from pathlib import Path

from heycomputer import reaper_mcp_client as mcp


class MCPSchemaTests(unittest.TestCase):
    def test_object_arguments_are_closed_and_required_fields_are_not_defaulted(self):
        schema = {"type": "object", "title": "Example", "properties": {
            "name": {"type": "string", "default": "Track"},
        }, "required": ["name"]}
        self.assertTrue(mcp.supported_schema(schema))
        mcp.validate_schema(schema, {"name": "Track"})
        for args in ({}, [], {"name": 1}, {"name": "Track", "extra": 0}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                mcp.validate_schema(schema, args)

    def test_array_items_enforce_nested_properties_and_required_fields(self):
        schema = {"type": "object", "properties": {
            "notes": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
                "type": "object", "additionalProperties": True,
                "properties": {"pitch": {"type": "integer", "minimum": 0, "maximum": 127}},
                "required": ["pitch"],
            }},
        }}
        self.assertTrue(mcp.supported_schema(schema))
        mcp.validate_schema(schema, {"notes": [{"pitch": 0}, {"pitch": 127}]})
        for notes in ("not an array", [], [None], [{}], [{"pitch": True}],
                      [{"pitch": 60.0}], [{"pitch": -1}], [{"pitch": 128}],
                      [{"pitch": 60, "extra": 0}], [{"pitch": 60}] * 3):
            with self.subTest(notes=notes), self.assertRaises(ValueError):
                mcp.validate_schema(schema, {"notes": notes})

    def test_scalar_constraints_and_types(self):
        schema = {"type": "object", "properties": {
            "mode": {"type": "string", "enum": ["trim", "fade"], "minLength": 4, "maxLength": 4},
            "amount": {"type": "number", "minimum": -1, "maximum": 1, "exclusiveMinimum": -1},
            "enabled": {"type": "boolean"},
            "choice": {"type": "number", "enum": [True, 2]},
        }}
        self.assertTrue(mcp.supported_schema(schema))
        mcp.validate_schema(schema, {"mode": "trim", "amount": 1, "enabled": False, "choice": 2.0})
        for key, value in (("mode", "play"), ("amount", -1), ("amount", 1.1),
                           ("amount", "0"), ("amount", True), ("amount", float("nan")),
                           ("amount", float("inf")), ("enabled", 1), ("choice", 1)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                mcp.validate_schema(schema, {key: value})
        string_only = {"type": "object", "properties": {"name": {
            "type": "string", "minLength": 1, "maxLength": 2,
        }}}
        mcp.validate_schema(string_only, {"name": "éb"})
        for name in ("", "abc"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                mcp.validate_schema(string_only, {"name": name})

    def test_published_nullable_argument(self):
        schema = {"type": "object", "properties": {"name": {
            "anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
            "default": None, "title": "Name",
        }}}
        self.assertTrue(mcp.supported_schema(schema))
        for args in ({}, {"name": None}, {"name": "Verse"}):
            with self.subTest(args=args):
                mcp.validate_schema(schema, args)
        for name in ("", [], 0, False):
            with self.subTest(name=name), self.assertRaises(ValueError):
                mcp.validate_schema(schema, {"name": name})

    def test_unsupported_or_malformed_optional_constraints_block_the_entire_schema(self):
        for child in (
            {"type": "string", "pattern": "example"},
            {"type": "integer", "minimum": True},
            {"type": "integer", "maximum": float("inf")},
            {"type": "string", "maxLength": -1},
            {"type": "string", "enum": "mode"},
            {"type": "object", "required": ["undeclared"]},
            {"type": "object", "additionalProperties": {"type": "integer"}},
            {"type": "array", "items": [{"type": "integer"}]},
            {"anyOf": [{"type": "null"}, {"$ref": "#/other"}]},
            {"anyOf": [{"type": "null"}], "not": {"type": "null"}},
            {"anyOf": []},
        ):
            schema = {"type": "object", "properties": {"optional": child}}
            with self.subTest(child=child):
                self.assertFalse(mcp.supported_schema(schema))
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    mcp.validate_schema(schema, {})

    def test_supports_every_pinned_published_schema(self):
        schemas = json.loads(Path(__file__).with_name("mcp_083_schemas.json").read_text(encoding="utf-8"))
        self.assertTrue(schemas)
        for name, schema in schemas.items():
            with self.subTest(name=name):
                self.assertTrue(mcp.supported_schema(schema))
