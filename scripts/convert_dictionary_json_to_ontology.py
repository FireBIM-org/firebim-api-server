from __future__ import annotations

import argparse
from pathlib import Path

from app.lexicon.dictionary_ontology import convert_dictionary_json_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ontology-family", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    raw_text = Path(args.input).read_text(encoding="utf-8")
    conversion = convert_dictionary_json_text(
        raw_text,
        ontology_family=args.ontology_family,
        country=args.country,
        version=args.version,
    )
    Path(args.output).write_text(conversion.ttl, encoding="utf-8")


if __name__ == "__main__":
    main()
