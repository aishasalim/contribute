from hermes.adapters.base import Adapter


class Lever(Adapter):
    def __init__(self):
        super().__init__(
            hosts=("lever.co",),
            open_form_selectors=("a:has-text('Apply for this job')",),
            submit_selectors=(
                "button:has-text('Submit application')",
                "button[type=submit]",
            ),
        )
