from html.parser import HTMLParser

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main


class ShellDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body_children: list[tuple[str, str]] = []
        self.class_names: set[str] = set()
        self.icon_names: set[str] = set()
        self.ids: set[str] = set()
        self.module_scripts: list[str] = []
        self.skip_targets: list[str] = []
        # Desktop and mobile are separate navigation roots that have to stay
        # in step. Merged into one list, deleting a link from either root is
        # invisible as long as the other root still carries the same label.
        self.desktop_controls: list[str] = []
        self.mobile_controls: list[str] = []
        self._body_depth = 0
        self._nav_depth = 0
        self._mobile_nav_depth = 0
        self._control_depth = 0
        self._control_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "body":
            self._body_depth = 1
            return

        if self._body_depth == 1:
            self.body_children.append(("element", tag))
        if self._body_depth:
            self._body_depth += 1

        element_id = attributes.get("id")
        if element_id:
            self.ids.add(element_id)

        if tag == "script" and attributes.get("type") == "module":
            source = attributes.get("src")
            if source:
                self.module_scripts.append(source)

        classes = set((attributes.get("class") or "").split())
        self.class_names.update(classes)
        if icon_name := attributes.get("data-icon"):
            self.icon_names.add(icon_name)
        if tag == "a" and "skip-link" in classes:
            target = attributes.get("href")
            if target:
                self.skip_targets.append(target)

        if tag == "nav":
            self._nav_depth += 1
            if "mobile-navigation" in classes:
                self._mobile_nav_depth += 1

        if tag in {"a", "button"} and self._nav_depth:
            self._control_depth = 1
            self._control_text = []
        elif self._control_depth:
            self._control_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._control_depth:
            self._control_depth -= 1
            if self._control_depth == 0:
                label = " ".join("".join(self._control_text).split())
                if label:
                    if self._mobile_nav_depth:
                        self.mobile_controls.append(label)
                    else:
                        self.desktop_controls.append(label)

        if tag == "nav" and self._nav_depth:
            if self._mobile_nav_depth:
                self._mobile_nav_depth -= 1
            self._nav_depth -= 1

        if tag == "body":
            self._body_depth = 0
        elif self._body_depth:
            self._body_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._control_depth:
            self._control_text.append(data)

    def handle_comment(self, data: str) -> None:
        if self._body_depth == 1:
            self.body_children.append(("comment", data.strip()))


def test_shell_retires_the_wizard_and_loads_modules():
    response = TestClient(main.app).get("/")
    assert response.status_code == 200
    document = ShellDocument()
    document.feed(response.text)

    assert document.body_children[0][0] == "comment"
    assert document.body_children[0][1].startswith("DIRECTION CONTRACT")
    assert document.skip_targets == ["#workspace"]
    assert "workspace" in document.ids
    assert document.module_scripts == ["/static/app.js"]
    assert "stepper" not in document.ids
    assert set(document.desktop_controls) == {
        "Desk",
        "Library",
        "Creative Tonies",
        "Activity",
        "Settings",
    }
    assert set(document.mobile_controls) == {
        "Desk",
        "Library",
        "Creative Tonies",
        "More",
        "Activity",
        "Settings",
    }
    assert "persistent-utilities" in document.class_names
    assert "activityStatus" in document.ids
    assert "clock" in document.icon_names


def test_every_application_route_serves_the_shell_document():
    client = TestClient(main.app)
    for path in (
        "/",
        "/desk",
        "/collection/the-wind-in-the-willows",
        "/library",
        "/tonies",
        "/activity",
        "/settings",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        document = ShellDocument()
        document.feed(response.text)
        assert document.module_scripts == ["/static/app.js"], path


def test_only_the_current_screens_serve_the_shell_document():
    """Pin the served set by URL, so a retired screen cannot come back at all.

    Filtering by endpoint identity would miss a fresh wrapper function that
    returns the same document from a retired path, which is exactly how a
    deleted screen comes back.
    """
    served = {
        route.path
        for route in main.app.routes
        if isinstance(route, APIRoute) and "GET" in route.methods
        and not route.path.startswith("/api/")
    }

    # /healthz is the one path outside the API surface that is not a screen.
    assert served == {
        "/",
        "/desk",
        "/library",
        "/tonies",
        "/activity",
        "/settings",
        "/collection/{slug}",
        "/healthz",
    }


def test_the_retired_review_urls_exist_nowhere_and_answer_404():
    """The deleted screen is gone as a URL, whichever function might serve it."""
    paths = {getattr(route, "path", "") for route in main.app.routes}
    assert not {"/review", "/review/{slug}"} & paths

    client = TestClient(main.app)
    assert client.get("/review").status_code == 404
    assert client.get("/review/the-wind-in-the-willows").status_code == 404
