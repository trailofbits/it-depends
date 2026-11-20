"""An explicitly invoked, read-only explorer for completed dependency documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Input, Static, Tree

from it_depends.reporting import plain_text

if TYPE_CHECKING:
    from textual.binding import BindingType
    from textual.events import Resize
    from textual.widgets.tree import TreeNode

    from it_depends.explorer.model import SavedGraph, SavedNode

_NARROW_WIDTH = 80


@dataclass
class _Selection:
    kind: Literal["group", "component", "reference"]
    key: str
    loaded: bool = False


class _DependencyTree(Tree[_Selection]):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("right", "expand_branch", "Expand", show=False),
        Binding("left", "collapse_branch", "Collapse", show=False),
    ]

    def action_expand_branch(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and not node.is_expanded:
            node.expand()
        elif node.children:
            self.move_cursor(node.children[0])

    def action_collapse_branch(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded:
            node.collapse()
        elif node.parent is not None:
            self.move_cursor(node.parent)


class SavedExplorer(App[None]):
    """Browse package versions and follow saved dependency references without analysis."""

    CSS = """
    #summary { height: auto; padding: 0 1; }
    #search { margin: 0 1; }
    #body { height: 1fr; }
    #tree { width: 1fr; height: 1fr; padding: 0 1; }
    #details-pane { width: 1fr; height: 1fr; border-left: solid $accent; padding: 0 1; }
    #details { height: auto; }
    .narrow #body { layout: vertical; }
    .narrow #details-pane { width: 1fr; border-left: none; border-top: solid $accent; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Close", priority=True),
        Binding("/", "search", "Search"),
    ]

    def __init__(self, graph: SavedGraph, filename: str) -> None:
        """Initialize navigation state for one immutable saved graph."""
        super().__init__()
        self.graph = graph
        self.title = f"it-depends · {plain_text(filename)}"
        self.members: dict[str, tuple[str, ...]] = {}
        self.group_nodes: dict[str, TreeNode[_Selection]] = {}
        self.primary: dict[str, TreeNode[_Selection]] = {}

    @property
    def dependency_tree(self) -> Tree[_Selection]:
        """Return the tree whose node data is owned by this explorer."""
        return cast("Tree[_Selection]", self.query_one("#tree", Tree))

    def compose(self) -> ComposeResult:
        """Lay out search, dependency navigation, and scrollable component details."""
        yield Header()
        yield Static(
            Text(
                f"{self.graph.kind} · {len(self.graph.groups)} package names · "
                f"{len(self.graph.nodes)} saved components\n"
                "Expand packages and versions. Enter follows a dependency reference."
            ),
            id="summary",
        )
        yield Input(placeholder="Search packages, versions, or component references…", id="search")
        with Horizontal(id="body"):
            yield _DependencyTree(Text("Packages"), id="tree")
            with VerticalScroll(id="details-pane"):
                yield Static(Text("Choose a package or version to inspect."), id="details")
        yield Footer()

    def on_mount(self) -> None:
        """Populate package groups while leaving their versions lazy."""
        self._rebuild("")
        self.dependency_tree.focus()

    def on_resize(self, _event: Resize) -> None:
        """Stack navigation and details when the terminal is narrow."""
        self.set_class(self.size.width < _NARROW_WIDTH, "narrow")

    def action_search(self) -> None:
        """Focus the package search field."""
        self.query_one("#search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter saved components without changing their graph relationships."""
        self._rebuild(event.value.casefold())

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        """Return keyboard navigation to the filtered tree."""
        self.dependency_tree.focus()

    def _rebuild(self, query: str) -> None:
        tree = self.dependency_tree
        tree.clear()
        self.primary.clear()
        self.group_nodes.clear()
        self.members = {
            group: tuple(key for key in keys if query in self._search_text(self.graph.nodes[key]))
            for group, keys in self.graph.groups.items()
        }
        groups = sorted(
            self.members.items(),
            key=lambda item: (not any(self.graph.nodes[key].root for key in item[1]), item[0]),
        )
        for group, keys in groups:
            if not keys:
                continue
            root = any(self.graph.nodes[key].root for key in keys)
            label = f"{group} ({len(keys)} versions)" + (" [root]" if root else "")
            self.group_nodes[group] = tree.root.add(Text(plain_text(label)), _Selection("group", group))
        tree.root.expand()
        if tree.root.children:
            self.call_after_refresh(self._select_first)
        else:
            tree.root.add_leaf(Text("No matching packages"))
        self.query_one("#details", Static).update(Text("Choose a package or version to inspect."))

    def _select_first(self) -> None:
        tree = self.dependency_tree
        if tree.root.children:
            tree.move_cursor(tree.root.children[0])

    @staticmethod
    def _search_text(node: SavedNode) -> str:
        return f"{node.group} {node.version} {node.reference} {node.purl}".casefold()

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[_Selection]) -> None:
        """Create only the branch the user chooses to expand."""
        self._populate(event.node)

    def _populate(self, tree_node: TreeNode[_Selection]) -> None:
        selection = tree_node.data
        if selection is None or selection.loaded:
            return
        selection.loaded = True
        if selection.kind == "group" and self.group_nodes.get(selection.key) is tree_node:
            for key in self.members[selection.key]:
                node = self.graph.nodes[key]
                label = node.version or "Version not recorded"
                if node.missing:
                    label = "Missing component record"
                self.primary[key] = tree_node.add(
                    Text(plain_text(label)),
                    _Selection("component", key),
                    allow_expand=bool(node.edges),
                )
        elif selection.kind == "component" and self.primary.get(selection.key) is tree_node:
            self._dependencies(tree_node, self.graph.nodes[selection.key])

    def _dependencies(self, tree_node: TreeNode[_Selection], node: SavedNode) -> None:
        for edge in node.edges:
            label = f"{edge.name} {edge.constraint}".strip()
            branch = tree_node.add(Text(plain_text(label)), expand=True, allow_expand=bool(edge.targets or edge.note))
            if edge.note:
                branch.add_leaf(Text(plain_text(edge.note)))
            for target in edge.targets:
                dependency = self.graph.nodes[target]
                label = f"↪ {dependency.label}" + (" [missing]" if dependency.missing else "")
                branch.add_leaf(Text(plain_text(label)), _Selection("reference", target))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[_Selection]) -> None:
        """Show selected component metadata and incoming dependencies."""
        selection = event.node.data
        if selection is None:
            return
        if selection.kind == "group":
            detail = Text(
                plain_text(
                    f"{selection.key}\n{len(self.members.get(selection.key, ()))} saved versions\n\n"
                    "Expand this package to choose a version."
                )
            )
        else:
            detail = self._details(self.graph.nodes[selection.key])
        self.query_one("#details", Static).update(detail)

    def _details(self, node: SavedNode) -> Text:
        lines = [node.label]
        if node.root:
            lines.append("Root component")
        if node.missing:
            lines.append("This reference has no component record in the saved document.")
        if node.reference:
            lines.append(f"Reference: {node.reference}")
        if node.purl:
            lines.append(f"Package URL: {node.purl}")
        lines.extend(["", "Dependencies"])
        lines.extend(f"  {edge.name} {edge.constraint} {edge.note}".rstrip() for edge in node.edges)
        if not node.edges:
            lines.append("  None recorded")
        lines.extend(["", "Required by"])
        incoming = self.graph.incoming.get(node.node_id, ())
        lines.extend(f"  {self.graph.nodes[key].label}" for key in incoming)
        if not incoming:
            lines.append("  None recorded")
        lines.extend(self._annotations(node))
        return Text(plain_text("\n".join(lines)))

    @staticmethod
    def _annotations(node: SavedNode) -> list[str]:
        lines: list[str] = []
        if node.vulnerabilities:
            lines.extend(["", "Vulnerabilities", *node.vulnerabilities])
        if node.maintenance:
            lines.extend(["", "Maintenance"])
            lines.extend(f"  {name.replace('_', ' ')}: {value}" for name, value in node.maintenance)
        return lines

    def on_tree_node_selected(self, event: Tree.NodeSelected[_Selection]) -> None:
        """Follow references to canonical nodes, keeping shared dependencies and cycles finite."""
        selection = event.node.data
        if selection is not None and selection.kind == "reference":
            self._follow(selection.key)

    def _follow(self, key: str) -> None:
        group = self.graph.nodes[key].group
        if key not in self.members.get(group, ()):
            self.query_one("#search", Input).value = ""
            self.call_after_refresh(self._follow, key)
            return
        group_node = self.group_nodes[group]
        self._populate(group_node)
        group_node.expand()
        self.call_after_refresh(self._select_component, key)

    def _select_component(self, key: str) -> None:
        tree = self.dependency_tree
        tree.move_cursor(self.primary[key])
        tree.scroll_to_node(self.primary[key], animate=False)
        tree.focus()
