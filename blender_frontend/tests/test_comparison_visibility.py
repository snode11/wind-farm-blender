from types import SimpleNamespace
from wfrl_blender.deflection import ComparisonView


class VisibilityObject:
    def __init__(self):
        self.hidden = False
        self.hide_render = False
        self.writes = 0

    def hide_get(self):
        return self.hidden

    def hide_set(self, value):
        self.hidden = value
        self.writes += 1


def test_hidden_comparison_recovers_external_change_without_repeated_writes():
    obj = VisibilityObject()
    view = SimpleNamespace(objects=[obj], labels=['old label'])
    ComparisonView.visible(view, False)
    ComparisonView.visible(view, False)
    assert obj.writes == 1 and obj.hide_render and not view.labels
    obj.hidden = False  # External UI edit must not defeat the next update.
    ComparisonView.visible(view, False)
    assert obj.writes == 2
    ComparisonView.visible(view, True)
    assert obj.writes == 3 and not obj.hidden and not obj.hide_render
