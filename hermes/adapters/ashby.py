from hermes.adapters.base import Adapter


class Ashby(Adapter):
    def __init__(self):
        super().__init__(
            hosts=("ashbyhq.com",),
            open_form_selectors=(
                "a:has-text('Apply Now')",
                "button:has-text('Apply')",
            ),
            submit_selectors=(
                "button:has-text('Submit Application')",
                "button[type=submit]",
            ),
        )
