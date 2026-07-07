from app.tools._base import fn, str_prop, num_prop


class ReportTools:
    TOOLS = [
        fn("profit_report",
           "Report revenue, ingredient cost, packaging cost, and gross profit over a "
           "date range. Use this for ANY profit / revenue / cost question and ANY "
           "timeframe — this week, last month, yesterday, this quarter, a custom range, etc. "
           "Infer start_date and end_date (YYYY-MM-DD) from the user's question using today's "
           "date from the system prompt (e.g. 'last month' → the 1st to the last day of the "
           "previous calendar month). Pass both dates whenever the user names or implies a "
           "period. If no range is given at all, it defaults to the last 30 days.",
           {
               "start_date": str_prop("Inclusive start date, YYYY-MM-DD. Omit to default."),
               "end_date": str_prop("Inclusive end date, YYYY-MM-DD. Omit to default."),
           }),

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
