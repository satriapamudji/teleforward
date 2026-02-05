import re
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class TransformRule:
    """Represents a single transformation rule."""

    rule_type: str  # 'whitelist', 'blacklist', 'replace', 'prefix', 'suffix', 'strip_links', 'strip_mentions'
    pattern: str = ""
    replacement: str = ""
    is_regex: bool = False
    enabled: bool = True


@dataclass
class TransformResult:
    """Result of transformation process."""

    should_forward: bool = True
    original_text: str = ""
    transformed_text: str = ""
    matched_rules: list[str] = field(default_factory=list)
    blocked_by: Optional[str] = None


class MessageTransformer:
    """Handles message filtering and transformation."""

    def __init__(self, rules: Optional[list[TransformRule]] = None):
        self.rules = rules if rules is not None else []

    def add_rule(self, rule: TransformRule):
        self.rules.append(rule)

    def clear_rules(self):
        self.rules.clear()

    def set_rules(self, rules: list[TransformRule]):
        self.rules = rules

    def _match_pattern(self, text: str, pattern: str, is_regex: bool) -> bool:
        """Check if pattern matches text."""
        if not pattern:
            return False
        try:
            if is_regex:
                return bool(re.search(pattern, text, re.IGNORECASE))
            else:
                return pattern.lower() in text.lower()
        except re.error:
            return False

    def _apply_replacement(
        self, text: str, pattern: str, replacement: str, is_regex: bool
    ) -> str:
        """Apply text replacement."""
        try:
            if is_regex:
                return re.sub(pattern, replacement, text, flags=re.IGNORECASE)
            else:
                # Case-insensitive string replacement
                compiled = re.compile(re.escape(pattern), re.IGNORECASE)
                return compiled.sub(replacement, text)
        except re.error:
            return text

    def _strip_links(self, text: str) -> str:
        """Remove URLs from text."""
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        return re.sub(url_pattern, "", text).strip()

    def _strip_mentions(self, text: str) -> str:
        """Remove @mentions from text."""
        mention_pattern = r"@\w+"
        return re.sub(mention_pattern, "", text).strip()

    def _strip_telegram_links(self, text: str) -> str:
        """Remove Telegram-specific links (t.me, telegram.me)."""
        tg_pattern = r'https?://(?:t\.me|telegram\.me)/[^\s<>"{}|\\^`\[\]]+'
        return re.sub(tg_pattern, "", text).strip()

    def transform(self, text: str) -> TransformResult:
        """
        Apply all transformation rules to text.

        Order of operations:
        1. Check whitelist (if any exist, message must match at least one)
        2. Check blacklist (if any match, block the message)
        3. Apply strip rules (links, mentions)
        4. Apply replacements
        5. Apply prefix/suffix
        """
        result = TransformResult(
            should_forward=True, original_text=text, transformed_text=text
        )

        if not text:
            return result

        enabled_rules = [r for r in self.rules if r.enabled]

        # Separate rules by type
        whitelist_rules = [r for r in enabled_rules if r.rule_type == "whitelist"]
        blacklist_rules = [r for r in enabled_rules if r.rule_type == "blacklist"]
        strip_rules = [r for r in enabled_rules if r.rule_type.startswith("strip_")]
        replace_rules = [r for r in enabled_rules if r.rule_type == "replace"]
        prefix_rules = [r for r in enabled_rules if r.rule_type == "prefix"]
        suffix_rules = [r for r in enabled_rules if r.rule_type == "suffix"]

        # 1. Check whitelist (if any whitelist rules exist, at least one must match)
        if whitelist_rules:
            matched_whitelist = False
            for rule in whitelist_rules:
                if self._match_pattern(text, rule.pattern, rule.is_regex):
                    matched_whitelist = True
                    result.matched_rules.append(f"whitelist:{rule.pattern}")
                    break

            if not matched_whitelist:
                result.should_forward = False
                result.blocked_by = "No whitelist pattern matched"
                return result

        # 2. Check blacklist
        for rule in blacklist_rules:
            if self._match_pattern(text, rule.pattern, rule.is_regex):
                result.should_forward = False
                result.blocked_by = f"Blacklist matched: {rule.pattern}"
                result.matched_rules.append(f"blacklist:{rule.pattern}")
                return result

        # 3. Apply strip rules
        current_text = text
        for rule in strip_rules:
            if rule.rule_type == "strip_links":
                current_text = self._strip_links(current_text)
                result.matched_rules.append("strip_links")
            elif rule.rule_type == "strip_mentions":
                current_text = self._strip_mentions(current_text)
                result.matched_rules.append("strip_mentions")
            elif rule.rule_type == "strip_telegram_links":
                current_text = self._strip_telegram_links(current_text)
                result.matched_rules.append("strip_telegram_links")

        # 4. Apply replacements
        for rule in replace_rules:
            if self._match_pattern(current_text, rule.pattern, rule.is_regex):
                current_text = self._apply_replacement(
                    current_text, rule.pattern, rule.replacement, rule.is_regex
                )
                result.matched_rules.append(
                    f"replace:{rule.pattern}->{rule.replacement}"
                )

        # 5. Apply prefix
        for rule in prefix_rules:
            if rule.replacement:  # prefix uses 'replacement' field for the prefix text
                current_text = rule.replacement + current_text
                result.matched_rules.append(f"prefix:{rule.replacement}")

        # 6. Apply suffix
        for rule in suffix_rules:
            if rule.replacement:  # suffix uses 'replacement' field for the suffix text
                current_text = current_text + rule.replacement
                result.matched_rules.append(f"suffix:{rule.replacement}")

        # Clean up extra whitespace
        current_text = re.sub(r"\s+", " ", current_text).strip()

        result.transformed_text = current_text
        return result


def create_transformer_from_db_rules(db_rules: list) -> MessageTransformer:
    """Create a MessageTransformer from database TransformRule objects."""
    transformer = MessageTransformer()

    for db_rule in db_rules:
        rule = TransformRule(
            rule_type=db_rule.rule_type,
            pattern=db_rule.pattern or "",
            replacement=db_rule.replacement or "",
            is_regex=db_rule.is_regex,
            enabled=db_rule.enabled,
        )
        transformer.add_rule(rule)

    return transformer
