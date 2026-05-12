from app.tools._base import fn, str_prop, num_prop, enum_prop


class PaymentTools:
    TOOLS = [
        fn("record_payment",
           "Record a payment for an order",
           {
               "order_identifier": str_prop("Customer name, phone, or order UUID"),
               "amount": num_prop(),
               "method": enum_prop("Cash", "Paytm", "Bank Transfer"),
           },
           required=["order_identifier", "amount", "method"]),

        fn("payment_history",
           "View payment history",
           {"start_date": str_prop("YYYY-MM-DD"), "end_date": str_prop("YYYY-MM-DD")}),
    ]
