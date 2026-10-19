"""Split configured daily combat allowances at verified mission boundaries."""

from module.logger import logger
from tasks.activity.scheduling import schedule_activity_after_battle
from tasks.base.resource_scheduling import ResourceCoordinator
from tasks.mission_reward.planning import DailyBudget
from tasks.mission_reward.scheduling import should_schedule_mission_reward


class DailyCombatMixin:
    def _new_daily_combat_budget(self):
        return DailyBudget(
            self.config,
            "CombatFarm" if self._combat_is_farm_task() else "Combat",
            {
                "fast": self._combat_fast_count() if self._combat_should_use_fast() else 0,
                "repeat": self._repeat_combat_leif_count(),
            },
        )

    def _daily_combat_review(self, budget):
        ResourceCoordinator(self.config).request("Combat", keys=("stamina",))
        if budget.needs_daily:
            budget.request_review()
        if should_schedule_mission_reward(self.config):
            self.config.task_call("MissionReward", force_call=False)
        schedule_activity_after_battle(self.config)

    def _daily_combat_delay(self, budget, resource_need=None):
        if budget.needs_daily or budget.has_remaining or budget.pending is not None:
            self.config.task_delay(minute=budget.retry_minutes)
            if resource_need is not None and budget.pending is None:
                ResourceCoordinator(self.config).wait("Combat", "stamina", resource_need)
        else:
            self.config.task_delay(server_update=True)

    def _settle_daily_repeat_budget(self, session):
        ResourceCoordinator(self.config).request("Combat", keys=("stamina",))
        budget = self._daily_combat_budget
        if not budget.active:
            return
        pending = budget.pending
        # A manually adopted run is not proof that this executor's reserved
        # batch finished. Match the persisted intent, including across a reset.
        if pending is not None and session.get("daily_reservation") == pending:
            budget.confirm()
            if budget.needs_daily:
                budget.request_review()

    def _run_daily_combat_batch(self, budget, stamina: int, leif: int = 0) -> bool:
        """Run one batch and return to main before observing daily rewards.

        Pages:
            in: regular dungeon prepare page
            out: main on success; current recoverable page on failure
        """
        if budget.pending is not None:
            logger.warning("Combat: unresolved daily action; do not launch another batch")
            self._leave_to_main(skip_first_screenshot=True)
            self._daily_combat_delay(budget)
            return False

        choices = []
        fast_reason = None
        if self._combat_should_use_fast():
            cost = self._combat_stage_stamina_cost()
            affordable = min(self.COMBAT_MAX_FAST_COUNT, stamina // cost) if cost else 0
            decision = budget.choose("fast", affordable, stamina_per_unit=cost or 0)
            fast_reason = decision.reason
            choices.append(decision.reason)
            if decision.reason == "run":
                status, count = self._prepare_fast_combat(
                    stamina=stamina, count_limit=decision.count,
                    skip_first_screenshot=True,
                )
                if status == "ready":
                    budget.reserve("fast", count, extra=decision.extra)
                    success = self._run_fast_combat(skip_first_screenshot=True)
                    if success:
                        budget.confirm()
                        self._daily_combat_review(budget)
                    returned = self._leave_to_main(skip_first_screenshot=True)
                    self._daily_combat_delay(budget)
                    return success and returned
                if status == "failed":
                    self._leave_to_main(skip_first_screenshot=True)
                    self.config.task_delay(success=False)
                    return False
                budget.observe_capacity("fast", 0)

        # Original repeat units may use the user's leaf allowance. Extra units
        # must fit wholly inside freshly observed owned stamina, and force the
        # owned-stamina toggle without modifying the saved user setting.
        original = budget.remaining("repeat")
        # A configured allowance does not prove the resources exist. Publishing
        # zero capacity lets another executor stop waiting and fill the gap.
        affordable = (
            min(50, leif + (stamina // 80 if self._repeat_prioritize_stamina() else 0))
            if original else stamina // 80
        )
        decision = budget.choose("repeat", affordable, stamina_per_unit=80)
        choices.append(decision.reason)
        if decision.reason == "run":
            prepared = self._prepare_repeat_combat(
                leif_count=decision.count,
                prioritize_stamina=True if decision.extra else None,
                skip_first_screenshot=True,
            )
            if not prepared:
                self._leave_to_main(skip_first_screenshot=True)
                self.config.task_delay(success=False)
                return False
            count = self._repeat_combat_prepared_leif_count
            budget.reserve("repeat", count, extra=decision.extra)
            # Persist the launch intent BEFORE starting. A crash between the
            # consuming click and accepted marker can then recover this exact
            # session; a lost/unknown session never refunds the reservation.
            session = self._combat_runtime_build()
            session["daily_reservation"] = budget.pending
            self._combat_runtime_set(session)
            if not self._run_repeat_combat(skip_first_screenshot=True):
                self.config.task_delay(success=False)
                return False
            accepted = self._combat_runtime_build()
            accepted["daily_reservation"] = budget.pending
            self._combat_runtime_set(accepted)
            returned = self._leave_to_main(skip_first_screenshot=True)
            self._delay_running_repeat_combat()
            return returned

        if "review" in choices:
            self.config.task_call("MissionReward", force_call=False)
        returned = self._leave_to_main(skip_first_screenshot=True)
        needs = []
        if "review" not in choices and "uncertain" not in choices:
            cost = self._combat_stage_stamina_cost()
            if ((fast_reason == "wait" or budget.remaining("fast") > 0)
                    and cost and stamina < cost and self._combat_should_use_fast()):
                needs.append(cost)
            if ((decision.reason == "wait" or original > 0)
                    and (self._repeat_prioritize_stamina() or not original)
                    and stamina < 80):
                needs.append(80)
        self._daily_combat_delay(budget, resource_need=min(needs) if needs else None)
        return returned
