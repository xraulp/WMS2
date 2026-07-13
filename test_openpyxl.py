try:
    import openpyxl
    print("openpyxl version:", openpyxl.__version__)
    print("openpyxl path:", openpyxl.__file__)
except ImportError as e:
    print("ERROR:", e)
