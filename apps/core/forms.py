class BootstrapFormMixin:
    """Ajoute la classe Bootstrap `form-control`/`form-check-input` à tous
    les champs, pour ne pas avoir à styler chaque template de formulaire."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            css_class = "form-check-input" if getattr(widget, "input_type", None) == "checkbox" else "form-control"
            widget.attrs["class"] = f"{existing} {css_class}".strip()
