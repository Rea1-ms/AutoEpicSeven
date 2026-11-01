"""Handle the quota warning before the dimmed event options underneath it."""

from module.logger import logger
from tasks.dimensional_exploration.assets.assets_dimensional_exploration_event_recruitment import (
    EVENT_RECRUITMENT_LIMIT_CANCEL, EVENT_RECRUITMENT_LIMIT_CONFIRM,
)
from tasks.dimensional_exploration.events.event import event_branch


class EventRecruitmentLimitMixin:
    def handle_event_recruitment_limit(self, vision):
        branch = event_branch(self.event_memory.pending)
        if branch is None:
            # A restart without a receipt cannot identify the selected reward.
            # Cancel back to the complete options so the normal decision path
            # can persist its choice before returning to this warning.
            return self.click_action(EVENT_RECRUITMENT_LIMIT_CANCEL)
        if not branch.rewards or any(reward['kind'] != 'hero' for reward in branch.rewards):
            self.require_human("Recruitment quota warning does not match a known hero-only event reward.")
        if self.click_action(EVENT_RECRUITMENT_LIMIT_CONFIRM):
            # The warning positively states that no hero can be recruited.
            # Carry this intentional skip to the next ticket card; otherwise
            # its Recruit button would reopen an unusable roster. Confirmation
            # itself is neither recruitment nor event progress, and does not
            # authorize a leave modal until handle_victory checks that card.
            # The existing positive map transition clears both permissions.
            self._recruit_skipped = True
            logger.info("Exploration event recruitment skipped: the game confirms insufficient quota.")
            return True
        return False
