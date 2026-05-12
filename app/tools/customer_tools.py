from app.tools._base import fn, str_prop


class CustomerTools:
    TOOLS = [
        fn("create_customer",
           "Add a new customer. Ask for a default delivery address (optional).",
           {"name": str_prop(), "phone": str_prop(), "address": str_prop("Default delivery address")},
           required=["name", "phone"]),

        fn("get_customer",
           "Search customers by name or phone. Omit search to list all.",
           {"search": str_prop("Name or phone — omit to list all")}),
    ]
