import fitz  # PyMuPDF
print(fitz.__file__)

pdf_path = r'O:\Documents\__HOUSE\04. MAIN\725А-s_АС+ИС_Косарева Н,Н_pdf_Freeze.pdf'
doc = fitz.open(pdf_path)

dpi_target = 300
scale = dpi_target / 72
mat = fitz.Matrix(scale, scale)

for i, page in enumerate(doc):
    #pix = page.get_pixmap(matrix=mat)
    #pix.save(f"page_{i+1}.png")
    pix = page.get_pixmap()
    pix.save(f"page_{i+1}_72dpi.png")
