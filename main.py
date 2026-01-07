import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser
from tkinter import scrolledtext

class LogViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Log Viewer")
        self.root.geometry("800x600")

        # List of configured shadings
        self.shades = []

        # Create menu bar
        menubar = tk.Menu(root)
        root.config(menu=menubar)

        # File Menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open file...", command=self.open_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=root.quit)

        # Clean Menu
        clean_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Clean", menu=clean_menu)
        clean_menu.add_command(label="Clear lines containing...", command=self.clear_lines)
        clean_menu.add_command(label="Clear empty lines...", command=self.clear_empty_lines)

        # Configuration Menu
        config_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Configuration", menu=config_menu)
        config_menu.add_command(label="Manage shades...", command=self.manage_shades)

        # Text area with scroll
        self.text_area = scrolledtext.ScrolledText(
            root,
            wrap=tk.WORD,
            width=100,
            height=30,
            font=("Consolas", 10)
        )
        self.text_area.pack(expand=True, fill='both', padx=10, pady=10)

        # Variable to store the current file path
        self.current_file = None

    def open_file(self):
        """Opens a dialog to select and open a text file"""
        filepath = filedialog.askopenfilename(
            title="Select log file",
            filetypes=[
                ("Text files", "*.txt *.log"),
                ("All files", "*.*")
            ]
        )

        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as file:
                    content = file.read()
                    self.text_area.delete(1.0, tk.END)
                    self.text_area.insert(1.0, content)
                    self.current_file = filepath
                    self.root.title(f"Log Viewer - {filepath}")
                    self.apply_shades()
            except Exception as e:
                messagebox.showerror("Error", f"Could not open the file:\n{str(e)}")

    def clear_empty_lines(self):
        """Clears all empty lines from the text area"""
        content = self.text_area.get(1.0, tk.END).strip()
        if not content:
            messagebox.showwarning("Warning", "No content to clear")
            return

        lines = [line for line in content.split('\n') if line.strip()]  # Keep only non-empty lines
        self.text_area.delete(1.0, tk.END)
        self.text_area.insert(1.0, '\n'.join(lines))
        self.apply_shades()

    def clear_lines(self):
        """Shows a dialog to enter text and deletes lines that contain it"""
        content = self.text_area.get(1.0, tk.END).strip()
        if not content:
            messagebox.showwarning("Warning", "No content to clear")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Clear Lines")
        dialog.geometry("400x150")
        dialog.transient(self.root)
        dialog.grab_set()

        label = tk.Label(dialog, text="Enter the text to search for:")
        label.pack(pady=10)

        entry = tk.Entry(dialog, width=40)
        entry.pack(pady=5)
        entry.focus()

        def execute_clear():
            search_text = entry.get()
            if not search_text:
                messagebox.showwarning("Warning", "You must enter a text to search for")
                return

            content = self.text_area.get(1.0, tk.END)
            lines = content.split('\n')
            filtered_lines = [line for line in lines if search_text not in line]
            deleted_lines = len(lines) - len(filtered_lines)

            self.text_area.delete(1.0, tk.END)
            self.text_area.insert(1.0, '\n'.join(filtered_lines))

            dialog.destroy()
            self.apply_shades()
            messagebox.showinfo("Result", f"{deleted_lines} line(s) were deleted")

        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=15)

        btn_clear = tk.Button(button_frame, text="Clear", command=execute_clear, width=10)
        btn_clear.pack(side=tk.LEFT, padx=5)

        btn_cancel = tk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10)
        btn_cancel.pack(side=tk.LEFT, padx=5)

        entry.bind('<Return>', lambda e: execute_clear())

    def manage_shades(self):
        """Window to manage the shades"""
        dialog = tk.Toplevel(self.root)
        dialog.title("Manage Shades")
        dialog.geometry("500x400")
        dialog.transient(self.root)

        # Top frame with the list of shades
        list_frame = tk.Frame(dialog)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tk.Label(list_frame, text="Configured shades:", font=("Arial", 10, "bold")).pack(anchor=tk.W)

        # Listbox with scrollbar
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, height=10)
        listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        scrollbar.config(command=listbox.yview)

        def update_list():
            listbox.delete(0, tk.END)
            for i, shade in enumerate(self.shades):
                listbox.insert(tk.END, f"{i + 1}. '{shade['text']}' - Color: {shade['color']}")

        update_list()

        # Button frame
        btn_frame = tk.Frame(dialog)
        btn_frame.pack(pady=10)

        def add_shade():
            add_dialog = tk.Toplevel(dialog)
            add_dialog.title("Add Shade")
            add_dialog.geometry("400x200")
            add_dialog.transient(dialog)
            add_dialog.grab_set()

            tk.Label(add_dialog, text="Text to search:").pack(pady=5)
            text_entry = tk.Entry(add_dialog, width=40)
            text_entry.pack(pady=5)
            text_entry.focus()

            color_var = tk.StringVar(value="#FFFF00")
            color_label = tk.Label(add_dialog, text=f"Selected Color: {color_var.get()}", bg=color_var.get(), width=30, height=2)
            color_label.pack(pady=10)

            def select_color():
                color = colorchooser.askcolor(title="Select color")
                if color[1]:
                    color_var.set(color[1])
                    color_label.config(text=f"Selected Color: {color[1]}", bg=color[1])

            tk.Button(add_dialog, text="Select Color", command=select_color).pack(pady=5)

            def save():
                search_text = text_entry.get()
                if not search_text:
                    messagebox.showwarning("Warning", "You must enter a text")
                    return

                self.shades.append({"text": search_text, "color": color_var.get()})
                update_list()
                self.apply_shades()
                add_dialog.destroy()

            btn_frame_add = tk.Frame(add_dialog)
            btn_frame_add.pack(pady=10)

            tk.Button(btn_frame_add, text="Save", command=save, width=10).pack(side=tk.LEFT, padx=5)
            tk.Button(btn_frame_add, text="Cancel", command=add_dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

        def remove_shade():
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("Warning", "Select a shade to remove")
                return

            index = selection[0]
            del self.shades[index]
            update_list()
            self.apply_shades()

        tk.Button(btn_frame, text="Add", command=add_shade, width=12).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Remove", command=remove_shade, width=12).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Close", command=dialog.destroy, width=12).pack(side=tk.LEFT, padx=5)

    def apply_shades(self):
        """Applies the configured shades to the text"""
        # Delete all existing tags
        for tag in self.text_area.tag_names():
            self.text_area.tag_delete(tag)

        if not self.shades:
            return

        # Apply each shade
        for i, shade in enumerate(self.shades):
            tag_name = f"shade_{i}"
            self.text_area.tag_configure(tag_name, background=shade['color'])

            # Search the text in all content
            content = self.text_area.get(1.0, tk.END)
            lines = content.split('\n')

            for line_num, line in enumerate(lines, start=1):
                if shade['text'] in line:
                    start = f"{line_num}.0"
                    end = f"{line_num}.end"
                    self.text_area.tag_add(tag_name, start, end)

if __name__ == "__main__":
    root = tk.Tk()
    app = LogViewerApp(root)
    root.mainloop()
