from hermes.adapters.base import Adapter


class Greenhouse(Adapter):
    def __init__(self):
        super().__init__(
            hosts=("greenhouse.io", "greenhouse.com"),
            open_form_selectors=(
                "a:has-text('Apply for this job')",
                "button:has-text('Apply')",
            ),
            submit_selectors=(
                "input[type=submit]",
                "button:has-text('Submit Application')",
            ),
        )
