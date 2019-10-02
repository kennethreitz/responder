import inspect
import pytest

from responder import models


_default_query = "q=%7b%20hello%20%7d&name=myname&user_name=test_user"


@pytest.mark.parametrize(
    "query, expected",
    [
        pytest.param(
            _default_query,
            {"q": ["{ hello }"], "name": ["myname"], "user_name": ["test_user"]},
            id="parse query with unique keys",
        ),
        pytest.param(
            "q=1&q=2&q=3", {"q": ["1", "2", "3"]}, id="parse query with the same key"
        ),
    ],
)
def test_query_dict(query, expected):
    d = models.QueryDict(query)
    assert d == expected


def test_query_dict_get():
    d = models.QueryDict(_default_query)

    assert d["user_name"] == "test_user"
    assert d.get("key_none_exist") is None


def test_query_dict_get_list():
    d = models.QueryDict(_default_query)

    assert d.get_list("user_name") == ["test_user"]
    assert d.get_list("key_none_exist") == []
    assert d.get_list("key_none_exist", ["foo"]) == ["foo"]


def test_query_dict_items_list():
    d = models.QueryDict(_default_query)

    items_list = d.items_list()
    assert inspect.isgenerator(items_list)
    assert dict(items_list) == {
        "q": ["{ hello }"],
        "name": ["myname"],
        "user_name": ["test_user"],
    }


def test_query_dict_items():
    d = models.QueryDict(_default_query)

    items = d.items()
    assert inspect.isgenerator(items)
    assert dict(items) == {"q": "{ hello }", "name": "myname", "user_name": "test_user"}


def test_query_dict_get_last_item():
    multiple_item_query = "q=1&q=2&q=3&name=myname&user_name=test_user"
    default_last_q_item = "3"
    d = models.QueryDict(multiple_item_query)
    last_item_of_q = d["q"]
    assert last_item_of_q == default_last_q_item


def test_query_dict_get_wrong_key():
    wrong_key = "a"
    with pytest.raises(KeyError):
        d = models.QueryDict(_default_query)
        wrong_key_item = d[wrong_key]
        x = 1 / 1


def test_query_dict_get_empty_key():
    d = models.QueryDict(_default_query)
    d["empty_key"] = []
    empty_item = d["empty_key"]
    assert empty_item == []
