import pytest

from it_depends.cargo import _parse_workspace_member


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("serde 1.0.0 (path+file:///home/user/serde)", "serde"),
        ("my-crate 0.1.0 (/path/to/my-crate)", "my-crate"),
        ("path+file:///home/user/serde#serde@1.0.0", "serde"),
        ("path+file:///home/user/serde#serde", "serde"),
        ("path+file:///path/to/crate#my-crate@0.1.0", "my-crate"),
    ],
)
def test_parse_workspace_member(member: str, expected: str) -> None:
    assert _parse_workspace_member(member) == expected


def test_parse_workspace_member_unknown_format() -> None:
    result = _parse_workspace_member("something-unexpected")
    assert result == "something-unexpected"
