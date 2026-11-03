from app.services.location_policy import posting_matches_retrieval_location


def test_location_aliases_are_token_level_and_specific():
    assert posting_matches_retrieval_location("Bengaluru, India", False, "Bangalore")
    assert posting_matches_retrieval_location("New Delhi", False, "Delhi")
    assert not posting_matches_retrieval_location("Delhi", False, "New Delhi, India")
    assert not posting_matches_retrieval_location("India", False, "Bengaluru")


def test_country_filters_accept_concrete_postings_but_city_filters_reject_country_only():
    assert posting_matches_retrieval_location("Bengaluru, India", False, "India")
    assert posting_matches_retrieval_location("New York, United States", False, "United States")
    assert not posting_matches_retrieval_location("India", False, "Bengaluru")
    assert not posting_matches_retrieval_location("United States", False, "New York")


def test_aliases_and_malformed_filters_fail_closed_without_substring_matches():
    assert posting_matches_retrieval_location("Bengaluru, India", False, "Bangalore")
    assert posting_matches_retrieval_location("New Delhi, India", False, "Delhi")
    assert not posting_matches_retrieval_location("Bengaluru, India", False, "!")
    assert not posting_matches_retrieval_location("Bengaluru, India", False, "Bengal")


def test_empty_filter_is_unrestricted_but_malformed_is_closed():
    assert posting_matches_retrieval_location(None, True, "")
    assert posting_matches_retrieval_location("Anywhere", True, None)
    assert not posting_matches_retrieval_location("Bengaluru", False, "!!!")
    assert not posting_matches_retrieval_location("!!!", False, "Bengaluru")


def test_no_reverse_broad_match_or_substring_match():
    assert not posting_matches_retrieval_location("Bengaluru", False, "Bengal")
    assert not posting_matches_retrieval_location("India", False, "Bengaluru")
