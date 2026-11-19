from pipeline.collect import _cis_country

CIS = ["Armenia", "Russia"]


def test_city_named_like_country_not_excluded():
    assert _cis_country({"all_locations": "Armenia, Quindio, Colombia", "regions": ["Latin America"]}, CIS) is None


def test_country_in_location_excluded():
    assert _cis_country({"all_locations": "Yerevan, Armenia", "regions": []}, CIS) == "Armenia"


def test_country_in_regions_excluded():
    assert _cis_country({"all_locations": "", "regions": ["Russia"]}, CIS) == "Russia"
