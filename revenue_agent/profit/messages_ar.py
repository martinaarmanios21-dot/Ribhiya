"""Egyptian-Arabic versions of the upload messages (issues, notes, fatal errors) so the Arabic UI never shows English.

The ingest code writes messages in English (also used in logs and tests); `ar()` maps each known message pattern to
plain Arabic. An unknown message falls back to the English text rather than a wrong translation.
"""

from __future__ import annotations

import re

TYPE_AR = {"sales": "مبيعات", "product_costs": "تكلفة منتجات", "expenses": "مصاريف وفواتير", "returns": "مرتجعات"}
FIELD_AR = {"quantity": "الكمية", "date": "التاريخ", "currency": "العملة", "order_id": "رقم الطلب",
            "revenue": "قيمة البيع", "unit_price": "سعر الوحدة", "unit_cost": "تكلفة الوحدة", "amount": "المبلغ",
            "due_date": "تاريخ الاستحقاق", "discount": "الخصم", "shipping_charged": "رسوم التوصيل",
            "refund_amount": "قيمة المرتجع", "product": "المنتج"}

_PATTERNS: list[tuple[str, object]] = [
    (r"could not tell what this table contains", "مش قادر أعرف الجدول ده فيه إيه: اختار نوعه وحدد الأعمدة."),
    (r"this table could be '(\w+)' or '(\w+)'",
     lambda m: f"الجدول ده ممكن يكون {TYPE_AR.get(m[1], m[1])} أو {TYPE_AR.get(m[2], m[2])}: أكّد نوعه."),
    (r"(\d+) expense rows had their category inferred",
     lambda m: f"{m[1]} سطر مصاريف اتحدد نوعهم من الوصف أو المورد: راجع التصنيف."),
    (r"same as row (\d+); skipped", lambda m: f"نفس السطر {m[1]} بالظبط، فاتشال عشان مايتحسبش مرتين."),
    (r"'(.*)' read as a (payment|marketplace) fees? cost",
     lambda m: f"«{m[1]}» اتحسب {'رسوم دفع' if m[2] == 'payment' else 'عمولة منصة'} (تكلفة)، مش مرتجع."),
    (r"(\d+) manual adjustment lines \(net (.*)\) were left out",
     lambda m: f"{m[1]} سطر تسوية يدوية (صافيهم {m[2]}) ماتحسبوش لأنهم مش مبيعات ولا مرتجعات: راجعهم لو كانوا مبيعات فعلاً."),
    (r"negative line read as a return", "سطر بكمية أو قيمة بالسالب: اتحسب مرتجع (فلوس رجعت للعميل)."),
    (r"'(.*)' is a delivery line", lambda m: f"«{m[1]}» رسوم توصيل مش منتج: اتحسبت رسوم توصيل على الطلب."),
    (r"'(.*)' is a discount line", lambda m: f"«{m[1]}» خصم مش منتج: اتحسب خصم على الطلب."),
    (r"order (.*) has only a delivery line", lambda m: f"الطلب {m[1]} فيه رسوم توصيل بس من غير منتجات، فاتشال."),
    (r"order (.*) has only a discount line", lambda m: f"الطلب {m[1]} فيه خصم بس من غير منتجات، فاتشال."),
    (r"the order's discounts are larger than the order", "خصومات الطلب أكبر من قيمة الطلب نفسه: راجع الأرقام."),
    (r"quantity must be positive", "الكمية لازم تكون أكبر من صفر."),
    (r"quantity is not a number", "الكمية مش رقم."),
    (r"(\w+) is negative", lambda m: f"{FIELD_AR.get(m[1], m[1])} بالسالب."),
    (r"unknown currency '(.*)'", lambda m: f"عملة مش معروفة «{m[1]}»."),
    (r"date is empty", "التاريخ فاضي."),
    (r"(.*) read as (\d{4}-\d{2}-\d{2}) \(day-first\)",
     lambda m: f"{m[1]} اتقرا {m[2]} (اليوم الأول): اتأكد إنه صح."),
    (r"due date unreadable", "تاريخ الاستحقاق مش مفهوم."),
    (r"date: ", "التاريخ مش مفهوم."),
    (r"order ID is empty", "رقم الطلب فاضي."),
    (r"no revenue or unit price", "مفيش قيمة بيع ولا سعر وحدة."),
    (r"line total ≠ price × quantity", "إجمالي السطر مش بيساوي السعر × الكمية (اتاخد الإجمالي)."),
    (r"unit cost is empty", "تكلفة الوحدة فاضية."),
    (r"amount is empty|amount must be positive", "المبلغ فاضي أو مش موجب."),
    (r"more than (\d+) rows", lambda m: f"الملف فيه أكتر من {m[1]} سطر: الباقي ماتقراش. قسّمه على أكتر من ملف."),
    (r"(\w+): .*not a number|(\w+): invalid", lambda m: f"{FIELD_AR.get(m[1] or m[2], m[1] or m[2])} مش رقم مفهوم."),
    (r"expands to far more data", "ملف الـ Excel ده بيفك لحجم ضخم جداً مقارنة بحجمه: احفظه CSV وارفعه."),
    (r"more than (\d+) sheets", lambda m: f"ملف الـ Excel فيه أكتر من {m[1]} شيت: ارفع الشيتات اللي محتاجها بس."),
    (r"unsupported file type", "نوع الملف ده مش مدعوم: ارفع CSV أو Excel (xlsx) أو PDF فيه نص."),
    (r"file larger than (\d+) MB", lambda m: f"الملف أكبر من {m[1]} ميجا."),
    (r"file is empty|the file has no data rows", "الملف فاضي أو مفيهوش بيانات."),
    (r"not a valid \.xlsx workbook", "ده مش ملف Excel سليم (ملفات .xls القديمة مش مدعومة: احفظه .xlsx)."),
    (r"could not open the workbook", "مقدرتش أفتح ملف الـ Excel."),
    (r"password-protected", "ملف الـ PDF عليه باسورد."),
    (r"could not read the PDF", "مقدرتش أقرا ملف الـ PDF."),
    (r"no text found in the PDF", "الـ PDF ده صورة متصورة ومفيهوش نص. اكتب الأرقام المهمة بإيدك أو ارفع نسخة "
                                  "CSV أو Excel."),
    (r"no table found in this PDF", "مفيش جدول واضح في الـ PDF ده: اكتب الإجماليات بإيدك."),
    (r"(\w+) is required|required", "خانة مطلوبة فاضية."),
]
_COMPILED = [(re.compile(p, re.I), t) for p, t in _PATTERNS]


def ar(message: str | None) -> str | None:
    if not message:
        return message
    for rx, tpl in _COMPILED:
        m = rx.search(message)
        if m:
            return tpl(m) if callable(tpl) else tpl
    return message
