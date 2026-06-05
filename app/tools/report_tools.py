from app.tools._base import fn, str_prop, num_prop


class ReportTools:
    TOOLS = [
        fn("weekly_profit",
           "Show this week's profit report",
           {}),

        fn("generate_invoice",
           "Generate and send a PDF invoice for a customer's order. "
           "Call this directly when asked — do NOT call get_customer first. "
           "If the customer has multiple orders, use the most recent one unless a date is specified. "
           "Always ask 'Should I add GST or any tax?' before generating — if yes, ask the percentage.",
           {
               "customer_identifier": str_prop("Customer name or phone"),
               "delivery_date": str_prop("YYYY-MM-DD — filter by delivery date (for old/backdated orders)"),
               "tax_rate": num_prop("Tax percentage e.g. 5 for 5% GST. Omit or 0 for no tax."),
               "tax_label": str_prop("Tax label e.g. 'GST (5%)' — auto-generated if omitted"),
           },
           required=["customer_identifier"]),
    ]
