from __future__ import annotations

from io import BytesIO
from typing import Any


def build_markdown_report(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 评论洞察报告 - {payload.get('task_id', '')}")
    lines.append("")
    lines.append(f"- 总评论: {payload.get('total_reviews', 0)}")
    lines.append(f"- 正面: {payload.get('positive_count', 0)}")
    lines.append(f"- 中性: {payload.get('neutral_count', 0)}")
    lines.append(f"- 负面: {payload.get('negative_count', 0)}")
    lines.append("")
    lines.append("## 总结")
    lines.append(str(payload.get('summary', '')))
    lines.append("")
    lines.append("## SKU 问题排行")
    for item in payload.get('sku_items', []):
        lines.append(f"- {item['sku']}: {item['review_count']} 条，差评率 {item['negative_rate'] * 100:.1f}%")
    lines.append("")
    lines.append("## 差评原因")
    for item in payload.get('pain_points', []):
        lines.append(f"- {item['name']} ({item['count']}): {item['suggestion']}")
    lines.append("")
    lines.append("## 代表好评")
    for q in payload.get('quotes', {}).get('positive_quotes', []):
        lines.append(f"- {q}")
    lines.append("")
    lines.append("## 代表差评")
    for q in payload.get('quotes', {}).get('negative_quotes', []):
        lines.append(f"- {q}")
    return "\n".join(lines).strip() + "\n"


def build_pdf_report(payload: dict[str, Any]) -> bytes:
    # Keep the PDF generator simple and ASCII-safe for the current environment.
    # The markdown report remains the authoritative, fully Unicode-safe export.
    lines = [
        f"Report: {payload.get('task_id', '')}",
        f"Total: {payload.get('total_reviews', 0)} / Pos: {payload.get('positive_count', 0)} / Neu: {payload.get('neutral_count', 0)} / Neg: {payload.get('negative_count', 0)}",
        f"Summary: {payload.get('summary', '')}",
        "SKU ranking:",
    ]
    for item in payload.get('sku_items', []):
        lines.append(f"- {item['sku']}: {item['review_count']} reviews, negative {item['negative_rate'] * 100:.1f}%")
    lines.append("Pain points:")
    for item in payload.get('pain_points', []):
        lines.append(f"- {item['name']} ({item['count']}): {item['suggestion']}")
    lines.append("Positive quotes:")
    lines.extend([f"- {q}" for q in payload.get('quotes', {}).get('positive_quotes', [])])
    lines.append("Negative quotes:")
    lines.extend([f"- {q}" for q in payload.get('quotes', {}).get('negative_quotes', [])])

    content = "\n".join(lines)
    escaped = content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 50 780 Td 14 TL ({escaped[:6000]}) Tj ET".encode("latin-1", "ignore")
    objects = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n")
    objects.append(b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n")
    objects.append(b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n")
    objects.append(f"5 0 obj << /Length {len(stream)} >> stream\n".encode() + stream + b"\nendstream endobj\n")
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj)
    xref = pdf.tell()
    pdf.write(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for off in offsets[1:]:
        pdf.write(f"{off:010d} 00000 n \n".encode())
    pdf.write(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return pdf.getvalue()
