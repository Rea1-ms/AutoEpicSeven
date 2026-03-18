import module.config.server as server

from tasks.mail.assets.assets_mail import GOTO_LINK, RECEIVE, RECEIVE_CONFIRM_LINK
from tasks.mail.mail import Mail, MailRemainingCountState, MailRemainingState


class TestMailAssets:
    def test_button_available_keeps_cn_fallback_when_defined(self, monkeypatch):
        monkeypatch.setattr(server, "lang", "global_en")
        assert Mail._button_available(RECEIVE) is True
        assert Mail._button_available(GOTO_LINK) is False

    def test_skip_goto_link_recognition_only_depends_on_current_lang_assets(self, monkeypatch):
        monkeypatch.setattr(server, "lang", "global_cn")
        assert Mail._mail_has_link_asset() is True
        assert Mail._skip_goto_link_recognition() is False

        monkeypatch.setattr(server, "lang", "cn")
        assert Mail._mail_has_link_asset() is True
        assert Mail._skip_goto_link_recognition() is True

    def test_receive_confirm_link_asset_only_exists_on_global_cn(self, monkeypatch):
        monkeypatch.setattr(server, "lang", "global_cn")
        assert Mail._button_available(RECEIVE_CONFIRM_LINK) is True

        monkeypatch.setattr(server, "lang", "cn")
        assert Mail._button_available(RECEIVE_CONFIRM_LINK) is False


class TestMailParsing:
    def test_parse_remaining_mail_count_text_from_plain_label(self):
        state = Mail._parse_remaining_mail_count_text("剩余12封")
        assert state.valid is True
        assert state.value == 12

    def test_parse_remaining_mail_count_text_from_counter(self):
        state = Mail._parse_remaining_mail_count_text("12/100")
        assert state.valid is True
        assert state.value == 12


class TestMailClaimResult:
    @staticmethod
    def _count_state(value: int, raw_text: str | None = None) -> MailRemainingCountState:
        if raw_text is None:
            raw_text = str(value)
        return MailRemainingCountState(
            raw_text=raw_text,
            normalized_text=raw_text,
            value=value,
            valid=True,
        )

    @staticmethod
    def _remaining_state(text: str, unit: str = "h", value: int = 1) -> MailRemainingState:
        return MailRemainingState(
            raw_text=text,
            normalized_text=text,
            unit=unit,
            value=value,
            valid=True,
        )

    def test_resolve_mail_claim_result_uses_mail_count_drop(self):
        result = Mail._resolve_mail_claim_result(
            before_count=self._count_state(12),
            after_count=self._count_state(11),
            before_state=self._remaining_state("1小时"),
            after_state=self._remaining_state("1小时"),
            receive_available=True,
        )
        assert result is True

    def test_resolve_mail_claim_result_uses_top_mail_change_when_count_invalid(self):
        invalid_before = MailRemainingCountState(raw_text="", normalized_text="", valid=False)
        invalid_after = MailRemainingCountState(raw_text="", normalized_text="", valid=False)
        result = Mail._resolve_mail_claim_result(
            before_count=invalid_before,
            after_count=invalid_after,
            before_state=self._remaining_state("1小时"),
            after_state=self._remaining_state("10分", unit="m", value=10),
            receive_available=True,
        )
        assert result is True

    def test_resolve_mail_claim_result_detects_unchanged_mail(self):
        result = Mail._resolve_mail_claim_result(
            before_count=self._count_state(12),
            after_count=self._count_state(12),
            before_state=self._remaining_state("1小时"),
            after_state=self._remaining_state("1小时"),
            receive_available=True,
        )
        assert result is False
