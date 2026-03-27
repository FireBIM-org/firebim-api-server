from app.checks.engine import CheckEngine
from app.checks.result_models import RunOptions

RULE_ID = "room_area_rule"
GRAPH_ID = "debug-graph-1"

RULE_TTL = '''
PREFIX ex: <http://example.org/firebim/>
PREFIX fb: <http://example.org/firebim/rule/>
PREFIX sh: <http://www.w3.org/ns/shacl#>

ex:missing_room_area  a  ex:RuleElement;
        ex:label     "Missing room area";
        ex:nodeType  "end";
        ex:value     "missing" .

ex:small_room  a     ex:RuleElement;
        ex:label     "Small room";
        ex:nodeType  "end";
        ex:value     "small" .

ex:check_room_area  a   sh:NodeShape;
        ex:label        "Check room area";
        ex:nodeType     "decision";
        fb:falsePath    ex:small_room;
        fb:truePath     ex:large_room;
        fb:unknownPath  ex:missing_room_area;
        sh:rule         [ a             sh:SPARQLRule;
                          sh:construct  """
        CONSTRUCT {
            ?this fb:ruleResult ?result .
        }
        WHERE {
            OPTIONAL { ?this <http://example.org/fbo/1.0/roomArea> ?h }

            BIND(
                IF(!BOUND(?h), fb:Unknown,
                    IF(?h > 120, fb:True, fb:False)
                ) AS ?result
            )
        }
        """
                        ];
        sh:targetClass  <http://example.org/fbo/1.0/Room> .

ex:large_room  a     ex:RuleElement;
        ex:label     "Large room";
        ex:nodeType  "end";
        ex:value     "large" .

ex:room_area_rule  a   ex:RuleSet;
        ex:entryPoint  ex:check_room_area .
'''

GRAPH_TTL = '''
PREFIX fbo: <http://example.org/fbo/1.0/>
PREFIX exg: <http://example.org/graph/>

exg:RoomA a fbo:Room ;
    fbo:roomArea 140 .

exg:RoomB a fbo:Room ;
    fbo:roomArea 80 .

exg:RoomC a fbo:Room .
'''


def main() -> None:
    engine = CheckEngine()

    result = engine.run_rule(
        graph_id=GRAPH_ID,
        rule_id=RULE_ID,
        options=RunOptions(),
        graph_ttl=GRAPH_TTL,
        graph_uri=f"https://firebim.org/graphs/buildings/{GRAPH_ID}",
        rule_ttl=RULE_TTL,
    )

    print("=== SUMMARY ===")
    print(result.summary.model_dump())

    print("\n=== ENGINE ===")
    print(result.engine)

    print("\n=== COMPILED RULE ===")
    print(result.compiled_rule)

    print(f"\n=== TARGETS ({len(result.targets)}) ===")
    for i, target in enumerate(result.targets, start=1):
        print(f"\n--- TARGET {i} ---")
        print(target.model_dump())


if __name__ == "__main__":
    main()