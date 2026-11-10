import os
import pytesseract
from PIL import Image
from markitdown import MarkItDown
import fitz  # PyMuPDF
import arabic_reshaper
from bidi.algorithm import get_display

def fix_arabic_text(text: str) -> str:
    """إصلاح النص العربي المقلوب"""
    if not text:
        return text
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

def extract_text_from_file(file_path: str, file_ext: str) -> str:
    extracted_text = ""
    try:
        if file_ext == ".pdf":
            with fitz.open(file_path) as doc:
                text_pages = []
                for page in doc:
                    text = page.get_text("text", sort=True)
                    if text:
                        text = fix_arabic_text(text)
                        text_pages.append(text)
                extracted_text = "\n".join(text_pages)
        
        elif file_ext in [".png", ".jpg", ".jpeg"]:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image, lang='ara+eng')
            extracted_text = fix_arabic_text(text)
            
        elif file_ext in [".docx", ".pptx", ".xlsx"]:
            md = MarkItDown()
            extracted_text = md.convert(file_path).text_content
            extracted_text = fix_arabic_text(extracted_text)
            
    except Exception as e:
        print(f"Error during extraction: {e}")
        extracted_text = ""
        
    return extracted_text