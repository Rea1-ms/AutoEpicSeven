import traceback

import module.config.server as server
from module.base.button import ButtonWrapper
from tasks.base.assets.assets_base_page import *
from tasks.base.assets.assets_base_popup import AD_BUFF_X_CLOSE
from tasks.base.assets.assets_base_main_page import MENU, MENU_CLOSE, WHITE_STAR
from tasks.mission_reward.assets.assets_mission_reward_entry import (
    DAILY_TAB_CHECK,
    DAILY_TAB_ENTRY,
    MISSION_REWARD_CHECK,
    WEEKLY_TAB_CHECK,
    WEEKLY_TAB_ENTRY,
)
from tasks.secret_shop.assets.assets_secret_shop import SECRET_SHOP_CHECK
if server.lang == 'cn':
    from tasks.store.assets.assets_store_entries import STORE_CHECK
else:
    from tasks.store.assets.assets_store_current_entries import (
        COMMON_STORE_ENTRY,
        COMMON_STORE_CHECK,
        CONQUEST_POINTS_STORE_ENTRY,
        CONQUEST_POINTS_STORE_CHECK,
        FREE_STORE_ENTRY,
        FREE_STORE_CHECK,
        INHERITANCE_STONE_STORE_ENTRY,
        INHERITANCE_STONE_STORE_CHECK,
        STORE_CHECK,
    )
from tasks.arena.assets.assets_arena import (
    ARENA_CHECK,
    ARENA_COMMON_ENTRY,
    BATTLE_PASS_CHECK,
    BATTLE_PASS_ENTRY,
)
from tasks.combat.assets.assets_combat_configs_entry import (
    ALTER_CHECK,
    COMMON_ENTRY,
    HUNT_CHECK,
    SEASON_ENTRY,
    SEASON_CHECK,
    SPIRIT_ALTAR,
    URGENT_TASKS,
)
from tasks.combat.assets.assets_combat_repeat_entry import REPEAT_COMBAT_MENU
from tasks.knights.assets.assets_knights_main_page import (
    KNIGHTS_ACTIVITY_ENTRY,
    KNIGHTS_CHECK,
    TEAM_BATTLE_OPENING,
    WORLD_BOSS_CHECK,
    WORLD_BOSS_OPENING,
)
from tasks.knights.assets.assets_knights_gvg import (
    KNIGHTS_CREST,
    KNIGHTS_NOT_ENOUGH_PEOPLE,
)
from tasks.knights.assets.assets_knights_activity_support_entry import (
    SUPPORT_CHECK,
)
from tasks.knights.assets.assets_knights_activity_weekly_task_entry import (
    WEEKLY_TASK_CHECK,
    WEEKLY_TASK_ENTRY,
)
from tasks.mail.assets.assets_mail import SORTING_CRITERIA
from tasks.item.assets.assets_item_inventory import (
    EQUIPMENT_CHECK,
    EQUIPMENT_ENTRY,
    INVENTORY_CHECK,
)
from tasks.sanctuary.assets.assets_sanctuary import (
    ALCHEMISTS_TOWER,
    ALCHEMISTS_TOWER_CHECK,
    HEART_OF_EULERBIS,
    HEART_OF_EULERBIS_CHECK,
    FOREST_OF_ELVES,
)
from tasks.sanctuary.assets.assets_sanctuary_forest_of_elves import (
    ALTAR_OF_GROWTH,
)


class Page:
    # Key: str, page name like "page_main"
    # Value: Page, page instance
    all_pages = {}

    @classmethod
    def clear_connection(cls):
        for page in cls.all_pages.values():
            page.parent = None

    @classmethod
    def iter_linked_pages(cls, page, extra_links=None):
        # Runtime overlay exits should win over same-depth static fallbacks.
        # Example: menu opened from a sanctuary sub-page should prefer
        # MENU_CLOSE -> that sub-page instead of MENU_GOTO_MAIN when both
        # routes are otherwise equally short.
        if extra_links is not None:
            for destination in extra_links.get(page, {}):
                yield destination

        for destination in page.links:
            yield destination

    @classmethod
    def init_connection(cls, destination, extra_links=None):
        """
        Initialize shortest-path parents on the page graph.

        This graph is unweighted, so a reverse breadth-first search already
        gives the same minimum-hop route we want. The old comment called this
        "A*", but there has never been any heuristic or edge-cost handling in
        the implementation.

        `extra_links` is reserved for runtime-only overlay exits, such as:
        - menu close -> the page that opened the menu
        - popup close -> the page that opened the popup

        Keep these links out of the static graph:
        1. they only exist after a real click has opened the overlay
        2. their destination depends on the current session context
        3. most routes should still be explainable by the static page graph

        Args:
            destination (Page):
            extra_links (dict[Page, dict[Page, ButtonWrapper]] | None):
        """
        cls.clear_connection()

        visited = [destination]
        visited = set(visited)
        while 1:
            new = visited.copy()
            for page in cls.iter_pages():
                if page in visited:
                    continue

                for linked_page in cls.iter_linked_pages(page, extra_links=extra_links):
                    if linked_page not in visited:
                        continue
                    page.parent = linked_page
                    new.add(page)
                    break
            if len(new) == len(visited):
                break
            visited = new

    @classmethod
    def iter_pages(cls):
        return cls.all_pages.values()

    @classmethod
    def iter_check_buttons(cls):
        for page in cls.all_pages.values():
            yield page.check_button

    def __init__(self, check_button, dynamic_return_button=None, dynamic_return_group=None):
        self.check_button = check_button
        self.links = {}
        self.dynamic_return_button = dynamic_return_button
        (filename, line_number, function_name, text) = traceback.extract_stack()[-2]
        self.name = text[:text.find('=')].strip()
        self.dynamic_return_group = dynamic_return_group or self.name
        self.parent = None
        Page.all_pages[self.name] = self

    def __eq__(self, other):
        if not isinstance(other, Page):
            return False
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        return self.name

    def link(self, button, destination):
        self.links[destination] = button


def link_shared_toolbar(*pages):
    """
    Register the top-right toolbar shared by most level-1 / level-2 E7 pages.

    This is intentionally modeled as explicit graph edges instead of ad-hoc
    route hacks, because these buttons are part of the normal page structure:
    - inventory may restore either the inventory container or equipment tab
    - mail is a direct jump target
    - menu is an overlay that should be reachable from many hub pages

    The helper only wires pages where these controls are expected to be stable.
    Deep combat / popup states are excluded on purpose.
    """
    for page in pages:
        if page not in (page_inventory, page_inventory_equipment):
            page.link(MAIN_GOTO_INVENTORY, destination=page_inventory)
            page.link(MAIN_GOTO_INVENTORY, destination=page_inventory_equipment)

        if page != page_mail:
            page.link(MAIN_GOTO_MAIL, destination=page_mail)

        if page != page_menu:
            page.link(MENU, destination=page_menu)


# Main page (use a stable main-page marker)
page_main = Page(WHITE_STAR)

# Menu (overlay/phone menu)
#
# MENU_CLOSE returns to the page that opened the menu, not a fixed page.
# The static graph keeps MENU_GOTO_MAIN as the stable fallback, while UI code
# injects a runtime-only close -> previous-page edge when that context exists.
page_menu = Page(
    MENU_CLOSE,
    dynamic_return_button=MENU_CLOSE,
    dynamic_return_group='overlay_menu',
)
page_menu.link(MENU_GOTO_MAIN, destination=page_main)
page_main.link(MENU, destination=page_menu)

# Gacha
page_gacha = Page(GACHA_CHECK)
page_gacha.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_GACHA, destination=page_gacha)

# Sanctuary
page_sanctuary = Page(SANCTUARY_CHECK)
page_sanctuary.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_SANCTUARY, destination=page_sanctuary)

# Sanctuary sub pages
page_sanctuary_forest = Page(ALTAR_OF_GROWTH)
page_sanctuary_forest.link(BACK, destination=page_sanctuary)
page_sanctuary.link(FOREST_OF_ELVES, destination=page_sanctuary_forest)

page_sanctuary_tower = Page(ALCHEMISTS_TOWER_CHECK)
page_sanctuary_tower.link(BACK, destination=page_sanctuary)
page_sanctuary.link(ALCHEMISTS_TOWER, destination=page_sanctuary_tower)

page_sanctuary_heart = Page(HEART_OF_EULERBIS_CHECK)
page_sanctuary_heart.link(BACK, destination=page_sanctuary)
page_sanctuary.link(HEART_OF_EULERBIS, destination=page_sanctuary_heart)

# Secret shop
page_secret_shop = Page(SECRET_SHOP_CHECK)
page_secret_shop.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_SECRET_SHOP, destination=page_secret_shop)

# Mail
# Mail is another toolbar overlay page. BACK should normally return to the
# page that opened it, not always page_main. Keep page_main as static fallback
# for manual starts where no runtime origin is known yet.
page_mail = Page(
    SORTING_CRITERIA,
    dynamic_return_button=BACK,
    dynamic_return_group='overlay_mail',
)
page_mail.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_MAIL, destination=page_mail)

# Inventory container page: entering inventory may restore either the default tab
# or the equipment tab depending on the last opened state.
#
# Inventory behaves like a toolbar overlay as well:
# - entering it from another page should preserve that page as the return
#   destination for BACK
# - switching between inventory tabs must not overwrite that return origin
#
# Therefore the container page and equipment tab share the same dynamic return
# group so internal tab jumps stay inside one overlay family.
page_inventory = Page(
    (INVENTORY_CHECK, EQUIPMENT_CHECK),
    dynamic_return_button=BACK,
    dynamic_return_group='overlay_inventory',
)
page_inventory.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_INVENTORY, destination=page_inventory)

page_inventory_equipment = Page(
    EQUIPMENT_CHECK,
    dynamic_return_button=BACK,
    dynamic_return_group='overlay_inventory',
)
page_inventory_equipment.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_INVENTORY, destination=page_inventory_equipment)
page_inventory.link(EQUIPMENT_ENTRY, destination=page_inventory_equipment)

# Store
#
# Store page registration must stay server-specific.
# ui_get_current_page()/ui_goto() iterate over every registered Page, so if CN
# registers current-store-only checks such as FREE_STORE_CHECK, any unrelated
# task can explode during route probing before it ever reaches its true target.
# Keep the global page graph aligned with the active assets family instead of
# trying to rely on runtime fallback behavior here.
page_store = Page(STORE_CHECK)
page_store.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_STORE, destination=page_store)

if server.lang != 'cn':
    # Current top-bar checks overlap:
    # - COMMON_STORE_CHECK stays valid inside both free / inheritance pages
    # - store top-bar markers coexist with sub-store markers
    # Define the more specific current sub-pages first so ui_get_current_page()
    # does not stop at a broader container page too early.
    page_free_store = Page(FREE_STORE_CHECK)
    page_free_store.link(BACK, destination=page_main)

    page_inheritance_stone_store = Page(INHERITANCE_STONE_STORE_CHECK)
    page_inheritance_stone_store.link(BACK, destination=page_main)

    page_conquest_points_store = Page(CONQUEST_POINTS_STORE_CHECK)
    page_conquest_points_store.link(BACK, destination=page_main)

    page_common_store = Page(COMMON_STORE_CHECK)
    page_common_store.link(BACK, destination=page_main)

    # Enter common branch from store home
    page_store.link(COMMON_STORE_ENTRY, destination=page_common_store)
    page_store.link(COMMON_STORE_ENTRY, destination=page_free_store)

    # Enter conquest directly from store home
    page_store.link(CONQUEST_POINTS_STORE_ENTRY, destination=page_conquest_points_store)

    # Common branch internal switching
    page_common_store.link(FREE_STORE_ENTRY, destination=page_free_store)
    page_common_store.link(INHERITANCE_STONE_STORE_ENTRY, destination=page_inheritance_stone_store)
    page_free_store.link(INHERITANCE_STONE_STORE_ENTRY, destination=page_inheritance_stone_store)
    page_inheritance_stone_store.link(FREE_STORE_ENTRY, destination=page_free_store)

# Mission reward popup with daily / weekly tabs
#
# These checks belong to the same popup family. Closing the popup should return
# to the page that opened it, which is usually page_menu but can also be a
# runtime-tracked overlay chain. The static fallback stays on page_menu so
# routes remain valid even when the popup was opened manually before the task.
page_mission_reward_daily = Page(
    DAILY_TAB_CHECK,
    dynamic_return_button=AD_BUFF_X_CLOSE,
    dynamic_return_group='overlay_mission_reward',
)
page_mission_reward_daily.link(AD_BUFF_X_CLOSE, destination=page_menu)

page_mission_reward_weekly = Page(
    WEEKLY_TAB_CHECK,
    dynamic_return_button=AD_BUFF_X_CLOSE,
    dynamic_return_group='overlay_mission_reward',
)
page_mission_reward_weekly.link(AD_BUFF_X_CLOSE, destination=page_menu)

page_mission_reward = Page(
    MISSION_REWARD_CHECK,
    dynamic_return_button=AD_BUFF_X_CLOSE,
    dynamic_return_group='overlay_mission_reward',
)
page_mission_reward.link(AD_BUFF_X_CLOSE, destination=page_menu)
page_mission_reward.link(DAILY_TAB_ENTRY, destination=page_mission_reward_daily)
page_mission_reward.link(WEEKLY_TAB_ENTRY, destination=page_mission_reward_weekly)
page_mission_reward_daily.link(WEEKLY_TAB_ENTRY, destination=page_mission_reward_weekly)
page_mission_reward_weekly.link(DAILY_TAB_ENTRY, destination=page_mission_reward_daily)
page_menu.link(MENU_GOTO_MISSION_REWARD, destination=page_mission_reward)
page_menu.link(MENU_GOTO_MISSION_REWARD, destination=page_mission_reward_daily)

# Arena mode-selection popup
#
# Main entry and menu entry both land on the same mode-selection popup.
# Similar to mission reward, the close button returns to the page underneath
# the popup. Keep page_menu as static fallback because both popup sources
# can still recover through menu/main routing if the runtime origin is absent.
page_arena_mode_popup = Page(
    ARENA_COMMON_ENTRY,
    dynamic_return_button=AD_BUFF_X_CLOSE,
    dynamic_return_group='overlay_arena_mode',
)
page_arena_mode_popup.link(AD_BUFF_X_CLOSE, destination=page_menu)
page_main.link(MAIN_ARENA_ENTRY, destination=page_arena_mode_popup)
page_menu.link(MENU_ARENA_ENTRY, destination=page_arena_mode_popup)

# Arena
page_arena = Page(ARENA_CHECK)
page_arena.link(BACK, destination=page_main)
page_arena_mode_popup.link(ARENA_COMMON_ENTRY, destination=page_arena)

page_arena_battle_pass = Page(BATTLE_PASS_CHECK)
page_arena_battle_pass.link(BACK, destination=page_arena)
page_arena.link(BATTLE_PASS_ENTRY, destination=page_arena_battle_pass)

# Combat
# Combat season tab
page_combat_season = Page(SEASON_CHECK)
page_combat_season.link(BACK, destination=page_main)

# Combat common tab
page_combat_common = Page(SPIRIT_ALTAR)
page_combat_common.link(BACK, destination=page_main)

# Combat urgent tab
page_combat_urgent = Page(URGENT_TASKS)
page_combat_urgent.link(BACK, destination=page_main)

# Combat container page: entering combat may restore either season or common tab.
page_combat = Page((SEASON_CHECK, SPIRIT_ALTAR, URGENT_TASKS))
page_combat.link(BACK, destination=page_main)
page_main.link(MAIN_GOTO_COMBAT, destination=page_combat)
page_main.link(MAIN_GOTO_COMBAT, destination=page_combat_season)
page_main.link(MAIN_GOTO_COMBAT, destination=page_combat_common)
page_main.link(MAIN_GOTO_COMBAT, destination=page_combat_urgent)
page_combat_season.link(COMMON_ENTRY, destination=page_combat_common)
page_combat_common.link(SEASON_ENTRY, destination=page_combat_season)
page_combat_urgent.link(COMMON_ENTRY, destination=page_combat_common)
page_combat_urgent.link(SEASON_ENTRY, destination=page_combat_season)
page_menu.link(MENU_GOTO_COMBAT, destination=page_combat)
page_menu.link(MENU_GOTO_COMBAT, destination=page_combat_season)
page_menu.link(MENU_GOTO_COMBAT, destination=page_combat_common)
page_menu.link(MENU_GOTO_COMBAT, destination=page_combat_urgent)

# Combat stage selection page (element / grade)
page_combat_stage = Page((ALTER_CHECK, HUNT_CHECK))
page_combat_stage.link(BACK, destination=page_combat_common)

# Combat prepare page
page_combat_prepare = Page(REPEAT_COMBAT_MENU)
page_combat_prepare.link(BACK, destination=page_combat_stage)

# Pets
page_pets = Page(PETS_CHECK)
page_pets.link(BACK, destination=page_main)
page_menu.link(MENU_GOTO_PETS, destination=page_pets)


# Knights
page_knights = Page(KNIGHTS_CHECK)
page_knights.link(BACK, destination=page_main)
page_menu.link(MENU_GOTO_KNIGHTS, destination=page_knights)

page_knights_support = Page(SUPPORT_CHECK)
page_knights_support.link(AD_BUFF_X_CLOSE, destination=page_knights)
page_knights.link(KNIGHTS_ACTIVITY_ENTRY, destination=page_knights_support)

page_knights_weekly_task = Page(WEEKLY_TASK_CHECK)
page_knights_weekly_task.link(AD_BUFF_X_CLOSE, destination=page_knights)
page_knights_support.link(WEEKLY_TASK_ENTRY, destination=page_knights_weekly_task)

page_knights_world_boss = Page(WORLD_BOSS_CHECK)
page_knights_world_boss.link(BACK, destination=page_knights)
page_knights.link(WORLD_BOSS_OPENING, destination=page_knights_world_boss)

page_knights_team_battle = Page((KNIGHTS_CREST, KNIGHTS_NOT_ENOUGH_PEOPLE))
page_knights_team_battle.link(BACK, destination=page_knights)
page_knights.link(TEAM_BATTLE_OPENING, destination=page_knights_team_battle)

# Menu direct destinations that now have dedicated overlay assets.
page_menu.link(MENU_GOTO_GACHA, destination=page_gacha)
page_menu.link(MENU_GOTO_STORE, destination=page_store)

# Most stable hub pages share the same top-right toolbar.
link_shared_toolbar(
    page_main,
    page_gacha,
    page_sanctuary,
    page_sanctuary_forest,
    page_sanctuary_tower,
    page_sanctuary_heart,
    page_secret_shop,
    page_store,
    page_arena,
    page_arena_battle_pass,
    page_combat_season,
    page_combat_common,
    page_combat_urgent,
    page_combat_stage,
    page_combat_prepare,
    page_pets,
    page_knights,
    page_knights_world_boss,
    page_knights_team_battle,
)

if server.lang != 'cn':
    link_shared_toolbar(
        page_free_store,
        page_inheritance_stone_store,
        page_conquest_points_store,
        page_common_store,
    )
