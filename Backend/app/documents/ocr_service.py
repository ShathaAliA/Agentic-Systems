import os
import pdfplumber
import pytesseract
from PIL import Image
from markitdown import MarkItDown
import fitz  # PyMuPDF

def extract_text_from_file(file_path: str, file_ext: str) -> str:
    extracted_text = ""
    try:
        if file_ext == ".pdf":
            # نستخدم PyMuPDF بدل pdfplumber عشان العربي يطلع صح
            with fitz.open(file_path) as doc:
                text_pages = []
                for page in doc:
                    text = page.get_text("text", sort=True)
                    if text:
                        text_pages.append(text)
                extracted_text = "\n".join(text_pages)
        
        elif file_ext in [".png", ".jpg", ".jpeg"]:
            image = Image.open(file_path)
            # OCR للصور (يدعم العربي والإنجليزي)
            extracted_text = pytesseract.image_to_string(image, lang='ara+eng')
            
        elif file_ext in [".docx", ".pptx", ".xlsx"]:
            # استخدام MarkItDown لملفات الأوفيس
            md = MarkItDown()
            extracted_text = md.convert(file_path).text_content
            
    except Exception as e:
        print(f"Error during extraction: {e}")
        extracted_text = ""
        
    return extracted_text