"""
Утилита для извлечения текста из документов Word (.docx) в текстовые файлы.

Использует библиотеку python-docx для чтения DOCX-файлов и сохранения всех
параграфов в текстовый файл UTF-8 кодировки. Может использоваться как скрипт
с аргументами командной строки или как модуль с функцией extract_docx_to_text.
"""

from docx import Document
import sys
from pathlib import Path


def extract_docx_to_text(in_path: str | Path, out_path: str | Path) -> None:
    """
    Извлекает текст из Word-документа и сохраняет в текстовый файл.

    Открывает DOCX-файл, извлекает текст из всех параграфов и записывает их
    в выходной файл (по одному параграфу на строку) в кодировке UTF-8.

    Args:
        in_path (str|Path): путь к входному DOCX-файлу.
        out_path (str|Path): путь к результирующему текстовому файлу.

    Returns:
        None: ничего не возвращает.
    """
    in_path = Path(in_path)
    out_path = Path(out_path)

    doc = Document(in_path)
    with out_path.open("w", encoding="utf-8") as f:
        for para in doc.paragraphs:
            f.write(para.text + "\n")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        inp = sys.argv[1]
        outp = sys.argv[2]
    else:
        inp = r"w:\RandomCoffee\rcb\ТЗ Random Coffee.docx"
        outp = r"w:\RandomCoffee\rcb\scripts\tz_extracted.txt"
    extract_docx_to_text(inp, outp)
    print(f"Wrote extracted text to: {outp}")
