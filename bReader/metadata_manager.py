"""ParseResultCollector manager class for processed sections and chunks

Provides object-oriented interface for loading and saving metadata files
with automatic file synchronization on each operation.
"""

import json
import os
from typing import Any, Optional, Dict, List, Union
from datetime import datetime
from pathlib import Path


class ParseResultCollector:
    """ParseResultCollector manager with automatic file synchronization

    Loads data from file on each get() operation and saves to file on each set()/add() operation.
    Automatically creates nested paths in metadata structure.

    Default file structure:
    {
      "sections": {
        "0": {...},
        "1": {...}
      },
      "chunks": {
        "section_0": {
          "section_file": "path/to/section/file",
          "chunks": {
            "0": {...},
            "1": {...}
          }
        }
      }
    }
    """

    def __init__(self, metadata_file: str = "metadata.json", output_dir: str = 'processed_book'):
        """Initialize metadata manager

        Args:
            metadata_file: Path to metadata file (default: "metadata.json")
            output_dir: Base output directory for processed files (default: "processed_book")
        """
        self.metadata_file = metadata_file
        self.output_dir = output_dir
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Ensure metadata file and its directory exist with proper structure"""
        try:
            # Create directory if it doesn't exist
            metadata_dir = os.path.dirname(self.metadata_file) or '.'
            os.makedirs(metadata_dir, exist_ok=True)

            # Create file with initial structure if it doesn't exist
            if not os.path.exists(self.metadata_file):
                initial_data = {
                    "sections": {},
                    "chunks": {}
                }
                with open(self.metadata_file, 'w', encoding='utf-8') as f:
                    json.dump(initial_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise OSError(f"Could not initialize metadata file {self.metadata_file}: {e}")

    def _load_from_file(self) -> Dict[str, Any]:
        """Load metadata from file, ensuring proper structure

        Returns:
            Dictionary with metadata structure
        """
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Ensure we have proper structure
            if not isinstance(data, dict):
                data = {"sections": {}, "chunks": {}}

            # Ensure both main branches exist
            if "sections" not in data:
                data["sections"] = {}
            if "chunks" not in data:
                data["chunks"] = {}

            # Convert old format if needed
            if isinstance(data.get("sections"), list):
                sections_list = data["sections"]
                sections_dict = {}
                for item in sections_list:
                    if isinstance(item, dict) and 'idx' in item:
                        sections_dict[str(item['idx'])] = item
                data["sections"] = sections_dict

            if isinstance(data.get("chunks"), list):
                chunks_list = data["chunks"]
                chunks_dict = {}
                for item in chunks_list:
                    if isinstance(item, dict) and 'idx' in item:
                        chunks_dict[str(item['idx'])] = item
                data["chunks"] = chunks_dict

            return data

        except (FileNotFoundError, json.JSONDecodeError):
            # If file doesn't exist or is corrupted, create new structure
            self._ensure_file_exists()
            return {"sections": {}, "chunks": {}}
        except Exception as e:
            raise OSError(f"Could not load metadata from {self.metadata_file}: {e}")

    def _save_to_file(self, data: Dict[str, Any]) -> None:
        """Save metadata to file

        Args:
            data: Dictionary with metadata to save
        """
        try:
            # Ensure directory exists
            metadata_dir = os.path.dirname(self.metadata_file) or '.'
            os.makedirs(metadata_dir, exist_ok=True)

            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise OSError(f"Could not save metadata to {self.metadata_file}: {e}")

    def _create_nested_path(self, data: Dict[str, Any], path_parts: List[str]) -> Dict[str, Any]:
        """Create nested path in data structure if it doesn't exist

        Args:
            data: Data dictionary to modify
            path_parts: List of path components (e.g., ['chunks', 'section_0', 'chunks'])

        Returns:
            Reference to the deepest nested dictionary
        """
        current = data
        for part in path_parts:
            if part not in current:
                current[part] = {}
            current = current[part]
        return current

    def get(self, path: str, default: Any = None) -> Any:
        """Get value from metadata by path

        Reloads metadata from file on each call to ensure fresh data.

        Args:
            path: Dot-separated path (e.g., 'sections.0', 'chunks.section_0.chunks.1')
            default: Default value if path not found

        Returns:
            Value at path or default if not found

        Examples:
            meta.get('sections.0')
            meta.get('chunks.section_0.chunks.1')
            meta.get('sections.0.title', 'Unknown Title')
        """
        data = self._load_from_file()

        # Navigate through path
        current = data
        path_parts = path.split('.') if path else []

        try:
            for part in path_parts:
                current = current[part]
            return current
        except (KeyError, TypeError):
            return default

    def set(self, path: str, value: Any) -> None:
        """Set value in metadata by path

        Automatically creates nested paths if they don't exist.
        Saves metadata to file after setting value.

        Args:
            path: Dot-separated path (e.g., 'sections.0', 'chunks.section_0.chunks.1')
            value: Value to set

        Examples:
            meta.set('sections.0', {'idx': 0, 'title': 'Chapter 1'})
            meta.set('chunks.section_0.section_file', '/path/to/section.txt')
            meta.set('chunks.section_0.chunks.1.text_length', 1500)
        """
        data = self._load_from_file()

        if not path:
            raise ValueError("Path cannot be empty")

        path_parts = path.split('.')
        key = path_parts[-1]
        parent_path = path_parts[:-1]

        # Create nested path if needed
        if parent_path:
            parent = self._create_nested_path(data, parent_path)
            parent[key] = value
        else:
            data[key] = value

        self._save_to_file(data)

    def add(self, path: str, value: Dict[str, Any]) -> str:
        """Add new item to metadata with auto-generated index

        Automatically finds next available index and creates nested paths.
        Saves metadata to file after adding value.

        Args:
            path: Dot-separated path to parent container (e.g., 'sections', 'chunks.section_0.chunks')
            value: Dictionary with item data (should contain 'idx' field)

        Returns:
            String index of added item

        Examples:
            idx = meta.add('sections', {'idx': 0, 'title': 'Chapter 1'})
            idx = meta.add('chunks.section_0.chunks', {'idx': 1, 'text_length': 1500})
        """
        data = self._load_from_file()

        if not path:
            raise ValueError("Path cannot be empty")

        # Ensure path exists
        path_parts = path.split('.')
        container = self._create_nested_path(data, path_parts)

        # Determine next index
        if 'idx' in value:
            idx = value['idx']
        else:
            # Find max index and increment
            max_idx = -1
            if isinstance(container, dict):
                for key in container.keys():
                    try:
                        max_idx = max(max_idx, int(key))
                    except (ValueError, TypeError):
                        pass
            idx = max_idx + 1
            value['idx'] = idx

        # Add timestamp if not present
        if 'processed_at' not in value:
            value['processed_at'] = datetime.now().isoformat()

        # Add to container
        str_idx = str(idx)
        container[str_idx] = value

        self._save_to_file(data)
        return str_idx

    def delete(self, path: str) -> bool:
        """Delete item from metadata by path

        Args:
            path: Dot-separated path to item to delete

        Returns:
            True if item was deleted, False if not found
        """
        data = self._load_from_file()

        if not path:
            return False

        path_parts = path.split('.')
        key = path_parts[-1]
        parent_path = path_parts[:-1]

        try:
            # Navigate to parent
            current = data
            for part in parent_path:
                current = current[part]

            # Delete key if exists
            if key in current:
                del current[key]
                self._save_to_file(data)
                return True
            return False
        except (KeyError, TypeError):
            return False

    def exists(self, path: str) -> bool:
        """Check if path exists in metadata

        Args:
            path: Dot-separated path to check

        Returns:
            True if path exists, False otherwise
        """
        return self.get(path, object()) is not object()

    def get_all_sections(self) -> Dict[str, Any]:
        """Get all sections metadata

        Returns:
            Dictionary with all sections
        """
        return self.get('sections', {})

    def get_all_chunks(self) -> Dict[str, Any]:
        """Get all chunks metadata

        Returns:
            Dictionary with all chunks organized by sections
        """
        return self.get('chunks', {})

    def get_section_chunks(self, section_idx: int) -> Dict[str, Any]:
        """Get chunks for specific section

        Args:
            section_idx: Section index

        Returns:
            Dictionary with chunks for the section
        """
        section_key = f'section_{section_idx}'
        return self.get(f'chunks.{section_key}.chunks', {})

    def set_section_chunks(self, section_idx: int, section_file: str, chunks: Dict[str, Any]) -> None:
        """Set chunks for specific section

        Args:
            section_idx: Section index
            section_file: Path to section file
            chunks: Dictionary of chunks data
        """
        section_key = f'section_{section_idx}'
        self.set(f'chunks.{section_key}.section_file', section_file)
        self.set(f'chunks.{section_key}.chunks', chunks)

    def add_section_chunk(self, section_idx: int, chunk_data: Dict[str, Any]) -> str:
        """Add chunk to specific section

        Args:
            section_idx: Section index
            chunk_data: Chunk metadata

        Returns:
            String index of added chunk
        """
        section_key = f'section_{section_idx}'
        return self.add(f'chunks.{section_key}.chunks', chunk_data)

    def set_source_file(self, file_path: str) -> None:
        """Set source file path in top-level metadata

        Args:
            file_path: Path to source file being processed
        """
        self.set('source_file', file_path)

    def get_source_file(self, default: str = '') -> str:
        """Get source file path from top-level metadata

        Args:
            default: Default value if source_file not found

        Returns:
            Path to source file or default if not found
        """
        return self.get('source_file', default)

    def add_section(
            self,
            idx: int,
            section_title: str,
            section_content: str,
            section_summary_content: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add section with automatic file creation and metadata management

        Creates section file, summary file (if summary content provided), and adds metadata.

        Args:
            idx: Section index
            section_title: Section title
            section_content: Section content text
            section_summary_content: Pre-generated summary content (optional)

        Returns:
            Dictionary with section metadata
        """
        from pathlib import Path

        # Ensure output directories exist
        sections_dir = Path(self.output_dir) / 'sections'
        summaries_dir = Path(self.output_dir) / 'summaries'
        sections_dir.mkdir(parents=True, exist_ok=True)
        summaries_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename from title
        def sanitize_filename(text: str, max_length: int = 50) -> str:
            """Convert text to valid filename component"""
            if not text:
                return ""

            import re
            # Convert to lowercase
            text = text.lower()
            # Replace spaces and dashes with underscores
            text = re.sub(r'[\s\-]+', '_', text)
            # Remove special characters, keep only alphanumeric, underscores, and Cyrillic
            text = re.sub(r'[^\w\u0400-\u04FF]', '', text)
            # Remove consecutive underscores
            text = re.sub(r'_+', '_', text)
            # Remove leading/trailing underscores
            text = text.strip('_')
            # Limit length
            if len(text) > max_length:
                text = text[:max_length].rstrip('_')
            return text

        def generate_unique_filename(full_title: str, section_idx: int) -> str:
            """Generate unique filename from full section title and index"""
            sanitized = sanitize_filename(full_title)
            if sanitized:
                return f"{section_idx:04d}_{sanitized}"
            else:
                return f"{section_idx:04d}"

        unique_filename = generate_unique_filename(section_title, idx)

        # Save section file
        section_file = sections_dir / f'{unique_filename}.txt'
        with open(section_file, 'w', encoding='utf-8') as f:
            if section_title:
                f.write(f"=== {section_title} ===\n\n")
            f.write(section_content)

        # Save summary file if summary content is provided
        summary_file = None
        if section_summary_content:
            summary_file = summaries_dir / f'{unique_filename}.txt'
            with open(summary_file, 'w', encoding='utf-8') as f:
                if section_title:
                    f.write(f"=== {section_title} ===\n\n")
                f.write(section_summary_content)

        # Create section metadata
        section_metadata = {
            'idx': idx,
            'title': section_title,
            'section_file': str(section_file),
            'summary_file': str(summary_file) if summary_file else None,
            'processed_at': datetime.now().isoformat()
        }

        # Add to metadata
        self.set(f'sections.{idx}', section_metadata)

        return section_metadata

    def set_section_content(self, section_idx: int, section_content: str) -> bool:
        """Update content for existing section

        Args:
            section_idx: Section index
            section_content: New section content text

        Returns:
            True if section was updated, False if section not found
        """
        from pathlib import Path

        # Check if section exists in metadata
        section_meta = self.get(f'sections.{section_idx}')
        if not section_meta:
            return False

        # Get section title and filename info
        section_title = section_meta.get('title', '')
        section_file_path = section_meta.get('section_file', '')

        if not section_file_path:
            return False

        # Update section file with new content
        try:
            with open(section_file_path, 'w', encoding='utf-8') as f:
                if section_title:
                    f.write(f"=== {section_title} ===\n\n")
                f.write(section_content)

            # Update metadata with new timestamp
            section_meta['updated_at'] = datetime.now().isoformat()
            self.set(f'sections.{section_idx}', section_meta)

            return True
        except Exception as e:
            print(f"Error updating section content: {e}")
            return False

    def set_section_summary_content(self, section_idx: int, section_summary_content: str) -> bool:
        """Update summary content for existing section

        Args:
            section_idx: Section index
            section_summary_content: New summary content text

        Returns:
            True if section summary was updated, False if section not found
        """
        from pathlib import Path

        # Check if section exists in metadata
        section_meta = self.get(f'sections.{section_idx}')
        if not section_meta:
            return False

        # Get section title and create/update summary file
        section_title = section_meta.get('title', '')
        section_file_path = section_meta.get('section_file', '')

        if not section_file_path:
            return False

        # Generate summary filename based on section filename
        section_path = Path(section_file_path)
        summaries_dir = Path(self.output_dir) / 'summaries'
        summaries_dir.mkdir(parents=True, exist_ok=True)
        summary_file_path = summaries_dir / section_path.name

        try:
            # Write summary content
            with open(summary_file_path, 'w', encoding='utf-8') as f:
                if section_title:
                    f.write(f"=== {section_title} ===\n\n")
                f.write(section_summary_content)

            # Update metadata with summary file path and timestamp
            section_meta['summary_file'] = str(summary_file_path)
            section_meta['updated_at'] = datetime.now().isoformat()
            self.set(f'sections.{section_idx}', section_meta)

            return True
        except Exception as e:
            print(f"Error updating section summary: {e}")
            return False
