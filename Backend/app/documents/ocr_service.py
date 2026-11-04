import os
import pdfplumber
import pytesseract
from PIL import Image
from markitdown import MarkItDown

def extract_text_from_file(file_path: str, file_ext: str) -> str:
    extracted_text = ""
    try:
        if file_ext == ".pdf":
            with pdfplumber.open(file_path) as pdf:
                text_pages = [page.extract_text() for page in pdf.pages if page.extract_text()]
                extracted_text = "\n".join(text_pages)
        
        elif file_ext in [".png", ".jpg", ".jpeg"]:
            image = Image.open(file_path)
            extracted_text = pytesseract.image_to_string(image, lang='ara+eng')
            
        elif file_ext in [".docx", ".pptx", ".xlsx"]:
            # استخدام MarkItDown لاستخراج النص من ملفات الأوفيس
            md = MarkItDown()
            extracted_text = md.convert(file_path).text_content
            
    except Exception as e:
        print(f"Error during OCR/Text extraction: {e}")
        extracted_text = ""
        
    return extracted_text