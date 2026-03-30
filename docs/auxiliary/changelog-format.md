# Factorio Mod Documentation: Changelog Format

## 1. Overview
This document defines the format required to display mod changelogs in the in-game mod browsing GUI. 

*   **File Name:** `changelog.txt`
*   **Location:** Root of the mod folder (see **Mod Structure**).
*   **Mod Portal:** The Mod Portal does **not** require this format; it displays `changelog.txt` content as plain text.
*   **Errors:** Parsing errors are written to the `log file` found in the **user data directory**.

## 2. General Formatting Rules
The changelog is parsed line by line. Adherence to whitespace rules is critical to avoid confusing error messages.

*   **Empty Lines:** Completely empty lines are skipped by the parser.
    *   *Exception:* The line immediately following the **Version Section Start** line must **not** be empty.
*   **Tabs:** Do **not** use tabs.
*   **Trailing Spaces:** Do **not** leave whitespace at the end of lines.
*   **Editor Configuration:** It is recommended to configure your text editor to enforce "no tabs" and "trim trailing whitespace" automatically.

## 3. Structure Specification

A changelog consists of one or multiple **Version Sections**. Each section describes exactly one mod version.

### 3.1. Version Section Separator
Each version section must start with a separator line.
*   **Format:** Exactly 99 dashes.
*   **Example:**
    ```text
    ---------------------------------------------------------------------------------------------------
    ```

### 3.2. Version Line
The line immediately following the separator.
*   **Format:** Must start with exactly `Version: ` (note the space after the colon).
*   **Content:** The rest of the line is parsed as the version string.
*   **Version Format:** `number.number.number` (e.g., `major.minor.sub`).
    *   Example: `0.6.4`
    *   Range: Each number must be between `0` and `65535`.
    *   Invalid: `0.0.0` is considered invalid.
*   **Constraints:** 
    *   Mandatory.
    *   Cannot be empty.
    *   No two version sections may have the same version number.
*   **Example:**
    ```text
    Version: 0.12.35
    ```

### 3.3. Date Line (Optional)
*   **Format:** Must start with exactly `Date: ` (note the space after the colon).
*   **Content:** The rest of the line is parsed as the release date.
*   **Constraints:** 
    *   Optional.
    *   No format restrictions for the date string.
    *   Maximum one date line per version section.
*   **Example:**
    ```text
    Date: 01. 06. 2016
    ```

### 3.4. Category Line (Optional)
*   **Format:** Must start with exactly **2 spaces**.
*   **Content:** The remaining text is the category name.
*   **Suffix:** The line must end with a colon (`:`). The colon is removed when displayed in the GUI.
*   **Constraints:** 
    *   Optional.
    *   Must precede entry lines.
*   **Example:**
    ```text
  Features:
    ```

### 3.5. Entry Line
*   **Format:** Must start with exactly **4 spaces**, followed by a dash (`-`), followed by a space (` `).
*   **Multiline Entries:** 
    *   First line: 4 spaces + `- ` + text.
    *   Continuation lines: Must start with exactly **6 spaces**.
*   **Constraints:**
    *   Must be associated with a previous **Category** line within the same version section.
    *   No exact duplicate entries allowed within the same version and category.
    *   Individual lines in multiline entries are checked for duplicates against other multiline entries in the same category.
*   **Example (Single Line):**
    ```text
    - Fixed the missing title in character logistics window.
    ```
*   **Example (Multiline):**
    ```text
    - This is a multiline entry.
      There is some extra text here because it is needed for the example.
    ```

## 4. Recognized Categories
While any category name is allowed, the following are recognized by the game and sorted in front of the "All" tab in the GUI:

| Category | Category |
| :--- | :--- |
| Major Features | Features |
| Minor Features | Graphics |
| Sounds | Optimizations |
| Balancing | Combat Balancing |
| Circuit Network | Changes |
| Bugfixes | Modding |
| Scripting | Gui |
| Control | Translation |
| Debug | Ease of use |
| Info | Locale |
| Compatibility | |

## 5. Complete Example
```text
---------------------------------------------------------------------------------------------------
Version: 1.1.60
Date: 06. 06. 2022
  Features:
    - This is an entry in the "Features" category.
    - This is another entry in the "Features" category.
    - This general section is the 1.1.60 version section.
  Balancing:
    - This is a multiline entry in the "Balancing" category.
      There is some extra text here because it is needed for the example.
      Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
  Bugfixes:
    - Fixed that canceling syncing mods with a save would exit the GUI.
    - Fixed a desync when fast-replacing burner generators.
---------------------------------------------------------------------------------------------------
Version: 1.1.59
Date: 06. 05. 2022
  Bugfixes:
    - This general section is the 1.1.59 version section.
    - This is an entry in the "Bugfixes" category.
    - Fixed grenade shadows.
---------------------------------------------------------------------------------------------------
Version: 0.1.0
Date: 24. 12. 2012
  Major Features:
    - Initial release.
    - This general section is the 0.1.0 version section.
```