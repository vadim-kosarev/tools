# bReader

A tool to parse FB2 books, extract sections, and generate summaries using Ollama.

## Setup

1. Create a virtual environment:
   ```
   python -m venv .venv
   ```

2. Activate the virtual environment:
   - On Windows: `.\.venv\Scripts\activate`
   - On Linux/Mac: `source .venv/bin/activate`

3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Install Ollama:
   ```
   winget install Ollama.Ollama
   ```

5. Download the required model:
   ```
   ollama pull qwen2.5:14b-instruct
   ```

## Usage

Run the script with an FB2 file:
```
python parse_and_summarize.py <path_to_fb2_file>
```

The output will be saved in the `processed_book` directory by default.

## How It Works

The script recursively processes nested sections in FB2 files, handling hierarchical structures like:
- ЧАСТЬ ПЕРВАЯ (Part 1)
  - 1 (Chapter 1)
  - 2 (Chapter 2)
- ЧАСТЬ ВТОРАЯ (Part 2)
  - 1 (Chapter 1)
  - etc.

For each leaf section (sections without nested subsections), the script:
1. Extracts the section title and builds a hierarchical title like "ЧАСТЬ ПЕРВАЯ - 1"
2. Saves the section text to `sections/section_XXXX.txt`
3. Generates an AI summary (if section > 5000 chars) and saves to `summaries/summary_XXXX.txt`
4. Tracks all sections in `sections_metadata.json` with index, title, and file paths

### Output Structure
```
processed_book/
├── sections/
│   ├── section_0000.txt
│   ├── section_0001.txt
│   └── ...
├── summaries/
│   ├── summary_0000.txt
│   ├── summary_0001.txt
│   └── ...
└── sections_metadata.json
```

