from app.tools._base import fn, str_prop


class ReportTools:
    TOOLS = [
        fn("weekly_profit",
           "Show this week's profit report",
           {}),

        fn("generate_invoice",
           "Generate and send a PDF invoice for a customer's order",
           {
               "customer_identifier": str_prop(),
               "delivery_date": str_prop("YYYY-MM-DD — optional if only one order"),
           },
           required=["customer_identifier"]),
    ]
