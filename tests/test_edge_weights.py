from app.core.edge_weights import DEFAULT_SPEED_BY_HIGHWAY_KPH, GraphAttributeAnnotator


def test_parse_maxspeed_handles_mph():
    kph = GraphAttributeAnnotator.parse_maxspeed_kph("30 mph")
    assert round(kph, 1) == round(30 * 1.60934, 1)


def test_parse_maxspeed_handles_plain_kph():
    assert GraphAttributeAnnotator.parse_maxspeed_kph("50") == 50.0


def test_parse_maxspeed_handles_missing_and_junk_values():
    assert GraphAttributeAnnotator.parse_maxspeed_kph(None) is None
    assert GraphAttributeAnnotator.parse_maxspeed_kph("signals") is None
    assert GraphAttributeAnnotator.parse_maxspeed_kph("") is None


def test_parse_maxspeed_takes_minimum_of_a_list():
    assert GraphAttributeAnnotator.parse_maxspeed_kph(["50", "30"]) == 30.0


def test_speed_falls_back_to_highway_class():
    annotator = GraphAttributeAnnotator()
    speed = annotator.speed_kph_for_edge({"highway": "residential"})
    assert speed == DEFAULT_SPEED_BY_HIGHWAY_KPH["residential"]


def test_speed_falls_back_to_universal_default_for_unknown_highway():
    annotator = GraphAttributeAnnotator()
    speed = annotator.speed_kph_for_edge({"highway": "not_a_real_type"})
    assert speed == annotator.universal_default_speed_kph


def test_maxspeed_tag_wins_over_highway_default():
    annotator = GraphAttributeAnnotator()
    speed = annotator.speed_kph_for_edge({"highway": "residential", "maxspeed": "70"})
    assert speed == 70.0


def test_annotate_populates_length_speed_and_travel_time():
    import networkx as nx

    graph = nx.MultiDiGraph()
    graph.add_node(1, x=0.0, y=0.0)
    graph.add_node(2, x=0.0, y=0.01)
    graph.add_edge(1, 2, length=1000.0, highway="residential")

    GraphAttributeAnnotator().annotate(graph)

    data = graph.get_edge_data(1, 2)[0]
    assert data["length"] == 1000.0
    assert data["speed_kph"] == DEFAULT_SPEED_BY_HIGHWAY_KPH["residential"]
    assert data["travel_time"] == 1000.0 / (data["speed_kph"] * 1000.0 / 3600.0)
