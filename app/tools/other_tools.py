from app.tools._base import fn, str_prop


class OtherTools:
    TOOLS = [
        fn("connect_instagram",
           "Connect the owner's Instagram account to receive orders from DMs",
           {}),

        fn("set_order_template",
           "Save the owner's custom order form template. Call this when the owner shares "
           "the format/template they use to collect orders (e.g. a blank form with field names "
           "like Name, Phone, Delivery date, Weight, Flavour, etc.). "
           "Once saved, the agent will use it to parse future pasted orders automatically.",
           {"template": str_prop("The raw template text exactly as the owner provided it")},
           required=["template"]),

        fn("get_order_template",
           "Retrieve the owner's saved order template. Use this to remind yourself of the "
           "field names and structure before parsing a pasted order.",
           {}),
    ]
