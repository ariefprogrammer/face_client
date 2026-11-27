import tkinter as tk
from tkinter import ttk

root = tk.Tk()
root.title("Tes UI")
root.geometry("400x250")

log = tk.Text(root, height=5)
log.pack(fill="x", padx=10, pady=10)

def klik():
    log.insert("end", "Tombol berfungsi!\n")

ttk.Button(root, text="Tes Klik", command=klik).pack(pady=10)

root.mainloop()