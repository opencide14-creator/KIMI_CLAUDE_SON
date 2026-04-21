"""
Jinja2 YAML Template Engine for LLM Prompt Management.

This module loads and renders Jinja2 templates stored as YAML files.
Each template defines a system prompt and a user prompt template for
different task types (code generation, text generation, analysis, etc.).

Example template YAML file::

    name: "BLANK_CODE_GENERATION"
    output_type: CODE
    system_prompt: |
      You are an elite software engineer...
    user_template: |
      TASK: {{title}}
      DESCRIPTION: {{description}}
      ACCEPTANCE CRITERIA: {{acceptance_criteria_formatted}}
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import jinja2
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class Template:
    """Represents a loaded template for LLM prompt rendering.

    Attributes:
        name: Template identifier (e.g., "BLANK_CODE_GENERATION").
        output_type: Expected LLM output category — one of CODE, TEXT,
            ANALYSIS, COMMAND, or STRUCTURED_DATA.
        system_prompt: The system-level prompt sent to the LLM.
        user_template: Compiled Jinja2 template used to render the user
            message from a context dictionary.
    """

    name: str
    output_type: str
    system_prompt: str
    user_template: jinja2.Template


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class Jinja2TemplateEngine:
    """Manages loading and rendering of Jinja2 YAML templates.

    The engine scans a directory for ``*.yaml`` files, parses each one,
    compiles the Jinja2 user template, and stores everything in an internal
    dictionary keyed by template name.

    Attributes:
        templates_dir: Absolute path to the directory containing template
            YAML files.
        templates: Mapping of template name -> :class:`Template` instance.
        jinja_env: Shared :class:`jinja2.Environment` used to compile all
            user templates.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, templates_dir: str = "templates") -> None:
        """Initialise the template engine.

        Creates a shared Jinja2 environment with ``trim_blocks=True`` and
        ``lstrip_blocks=True`` to produce clean output, then loads every
        ``.yaml`` file found in *templates_dir*.

        Args:
            templates_dir: Path to the templates directory. Relative paths
                are resolved against the current working directory.
        """
        self.templates_dir: str = os.path.abspath(templates_dir)
        self.templates: dict[str, Template] = {}
        self.jinja_env: jinja2.Environment = jinja2.Environment(
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=jinja2.StrictUndefined,
        )
        self.load_templates()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_templates(self) -> None:
        """Scan *templates_dir* for ``.yaml`` files and load each one.

        For every YAML file found:

        1. Open and read the file contents.
        2. Parse with ``yaml.safe_load()``.
        3. Extract *name*, *output_type*, *system_prompt*, and
           *user_template* fields.
        4. Compile *user_template* into a Jinja2 :class:`Template`.
        5. Store the resulting :class:`Template` in ``self.templates``.

        Files that cannot be read or parsed are logged as warnings and
        skipped so that one broken template does not prevent the rest from
        loading.
        """
        if not os.path.isdir(self.templates_dir):
            logger.warning(
                "Templates directory does not exist: %s", self.templates_dir
            )
            return

        logger.info(
            "Scanning for template YAML files in %s", self.templates_dir
        )

        for filename in sorted(os.listdir(self.templates_dir)):
            if not filename.lower().endswith((".yaml", ".yml")):
                continue

            file_path = os.path.join(self.templates_dir, filename)
            self._load_single_template(file_path)

        logger.info(
            "Loaded %d template(s): %s",
            len(self.templates),
            list(self.templates.keys()),
        )

    def _load_single_template(self, file_path: str) -> None:
        """Load a single YAML template file.

        Args:
            file_path: Absolute path to the ``.yaml`` file.
        """
        filename = os.path.basename(file_path)

        # --- read file ------------------------------------------------
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                raw_content = fh.read()
        except OSError as exc:
            logger.warning("Cannot read template file %s: %s", filename, exc)
            return

        # --- parse YAML -----------------------------------------------
        try:
            data: dict[str, Any] = yaml.safe_load(raw_content)
        except yaml.YAMLError as exc:
            logger.warning(
                "YAML parse error in %s: %s", filename, exc
            )
            return

        if not isinstance(data, dict):
            logger.warning(
                "Top-level YAML structure in %s is not a mapping; skipping.",
                filename,
            )
            return

        # --- extract required fields ----------------------------------
        name = data.get("name")
        output_type = data.get("output_type")
        system_prompt = data.get("system_prompt")
        user_template_raw = data.get("user_template")

        if name is None:
            logger.warning(
                "Template file %s is missing required field 'name'; skipping.",
                filename,
            )
            return

        if system_prompt is None:
            logger.warning(
                "Template '%s' (%s) is missing 'system_prompt'; skipping.",
                name,
                filename,
            )
            return

        if user_template_raw is None:
            logger.warning(
                "Template '%s' (%s) is missing 'user_template'; skipping.",
                name,
                filename,
            )
            return

        # Normalise types
        name = str(name).strip()
        output_type = str(output_type).strip().upper() if output_type else "TEXT"
        system_prompt = str(system_prompt).strip()
        user_template_raw = str(user_template_raw)

        # --- compile Jinja2 user template -----------------------------
        try:
            compiled_user_template = self.jinja_env.from_string(
                user_template_raw
            )
        except jinja2.TemplateSyntaxError as exc:
            logger.warning(
                "Jinja2 syntax error in user_template of '%s' (%s): %s",
                name,
                filename,
                exc,
            )
            return

        # --- store ----------------------------------------------------
        self.templates[name] = Template(
            name=name,
            output_type=output_type,
            system_prompt=system_prompt,
            user_template=compiled_user_template,
        )
        logger.debug(
            "Registered template '%s' (output_type=%s) from %s",
            name,
            output_type,
            filename,
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def list_templates(self) -> list[str]:
        """Return a sorted list of all loaded template names.

        Returns:
            Alphabetically sorted list of template name strings.
        """
        return sorted(self.templates.keys())

    def get_template(self, name: str) -> Template:
        """Retrieve a :class:`Template` by its name.

        Args:
            name: Template name (case-sensitive).

        Returns:
            The matching :class:`Template` instance.

        Raises:
            ValueError: If no template with the given *name* is loaded.
        """
        if name not in self.templates:
            available = ", ".join(self.list_templates()) or "(none)"
            raise ValueError(
                f"Template '{name}' not found. Available: {available}"
            )
        return self.templates[name]

    def template_exists(self, name: str) -> bool:
        """Check whether a template with the given name is loaded.

        Args:
            name: Template name to check.

        Returns:
            ``True`` if the template exists, ``False`` otherwise.
        """
        return name in self.templates

    def get_template_info(self, name: str) -> dict[str, Any]:
        """Return summary metadata for a template.

        The returned dictionary contains:

        - **name** — Template name.
        - **output_type** — Output category.
        - **system_prompt_length** — Character length of the system prompt.
        - **has_user_template** — ``True`` if a user template is present
          (always ``True`` for successfully loaded templates).

        Args:
            name: Template name.

        Returns:
            Dictionary of template metadata.

        Raises:
            ValueError: If the template does not exist.
        """
        template = self.get_template(name)
        return {
            "name": template.name,
            "output_type": template.output_type,
            "system_prompt_length": len(template.system_prompt),
            "has_user_template": template.user_template is not None,
        }

    def get_system_prompt(self, template_name: str) -> str:
        """Return the system prompt for the named template.

        Args:
            template_name: Template name.

        Returns:
            System prompt string.

        Raises:
            ValueError: If the template does not exist.
        """
        return self.get_template(template_name).system_prompt

    def get_output_type(self, template_name: str) -> str:
        """Return the output type for the named template.

        Args:
            template_name: Template name.

        Returns:
            Output type string (e.g., ``"CODE"``, ``"TEXT"``).

        Raises:
            ValueError: If the template does not exist.
        """
        return self.get_template(template_name).output_type

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def render_user_message(self, template_name: str, context: dict) -> str:
        """Render the user template with the supplied context dictionary.

        Typical context keys:

        - ``title`` — SubTask title.
        - ``description`` — SubTask description.
        - ``acceptance_criteria_formatted`` — Bullet-list of acceptance
          criteria.
        - ``dependency_outputs`` — Combined outputs from parent tasks
          (string).
        - ``output_schema`` — JSON schema for structured-data tasks
          (optional).

        Args:
            template_name: Name of the template to render.
            context: Dictionary of variables available inside the Jinja2
                user template.

        Returns:
            Rendered user message string.

        Raises:
            ValueError: If the template does not exist.
            jinja2.UndefinedError: If the template references a variable
                missing from *context*.
        """
        template = self.get_template(template_name)
        try:
            rendered = template.user_template.render(**context)
        except jinja2.UndefinedError as exc:
            logger.error(
                "Rendering template '%s' failed — undefined variable: %s",
                template_name,
                exc,
            )
            raise
        return rendered.strip()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload_templates(self) -> None:
        """Clear all loaded templates and reload them from disk.

        This is useful when template files have been modified at runtime.
        """
        logger.info("Reloading all templates from %s", self.templates_dir)
        self.templates.clear()
        self.load_templates()
