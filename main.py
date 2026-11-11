import tkinter as tk

from app.gui import build_window


def main() -> None:
    root = tk.Tk()
    build_window(root)
    root.mainloop()


if __name__ == "__main__":
    main()
