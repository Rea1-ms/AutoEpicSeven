import traceback

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
from tasks.store.assets.assets_store import STORE_CHECK
from tasks.arena.assets.assets_arena import ARENA_CHECK, ARENA_COMMON_ENTRY, ARENA_ENTRY
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
from tasks.knights.assets.assets_knights import KNIGHTS_CHECK
from tasks.knights.assets.assets_knights_expedition import (
    EXPEDITION,
    KNIGHTS_CREST,
    READY_TO_FIGHT,
    TEAM_BATTLE,
    WAITING_FOR_WAR,
    WORLD_BOSS,
)
from tasks.knights.assets.assets_knights_donate import DONATE, DONATE_CHECK
from tasks.knights.assets.assets_knights_support import SUPPORT, SUPPORT_CHECK
from tasks.knights.assets.assets_knights_weekly_task import WEEKLY_TASK, WEEKLY_TASK_CHECK
from tasks.knights_v2.assets.assets_knights_v2_main_page import (
    KNIGHTS_ACTIVITY_ENTRY,
    KNIGHTS_CHECK as KNIGHTS_V2_CHECK,
    WORLD_BOSS_CHECK as WORLD_BOSS_V2_CHECK,
    WORLD_BOSS_ENTRY,
)
from tasks.knights_v2.assets.assets_knights_v2_gvg import KNIGHTS_CREST as KNIGHTS_V2_CREST
from tasks.knights_v2.assets.assets_knights_v2_activity_support_entry import (
    SUPPORT_CHECK as KNIGHTS_V2_SUPPORT_CHECK,
)
from tasks.knights_v2.assets.assets_knights_v2_activity_weekly_task_entry import (
    WEEKLY_TASK_CHECK as KNIGHTS_V2_WEEKLY_TASK_CHECK,
    WEEKLY_TASK_ENTRY as KNIGHTS_V2_WEEKLY_TASK_ENTRY,
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
    def init_connection(cls, destination):
        """
        Initialize an A* path finding among pages.

        Args:
            destination (Page):
        """
        cls.clear_connection()

        visited = [destination]
        visited = set(visited)
        while 1:
            new = visited.copy()
            for page in visited:
                for link in cls.iter_pages():
                    if link in visited:
                        continue
                    if page in link.links:
                        link.parent = page
                        new.add(link)
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

    def __init__(self, check_button):
        self.check_button = check_button
        self.links = {}
        (filename, line_number, function_name, text) = traceback.extract_stack()[-2]
        self.name = text[:text.find('=')].strip()
        self.parent = None
        Page.all_pages[self.name] = self

    def __eq__(self, other):
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        return self.name

    def link(self, button, destination):
        self.links[destination] = button


# Main page (use a stable main-page marker)
page_main = Page(WHITE_STAR)

# Menu (overlay/phone menu)
page_menu = Page(MENU_CLOSE)
page_menu.link(MENU_GOTO_MAIN, destination=page_main)
page_main.link(MENU, destination=page_menu)

# # Character
# page_character = Page(CHARACTER_CHECK)
# page_character.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_CHARACTER, destination=page_character)
#
# # Team
# page_team = Page(TEAM_CHECK)
# page_team.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_TEAM, destination=page_team)
#
# # Item, storage
# page_item = Page(ITEM_CHECK)
# page_item.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_ITEM, destination=page_item)
#
# # Guide, which includes beginners' guide, daily missions and dungeons
# page_guide = Page(GUIDE_CHECK)
# page_guide.link(GUIDE_CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_GUIDE, destination=page_guide)

# Gacha
page_gacha = Page(GACHA_CHECK)
page_gacha.link(MENU, destination=page_menu)
page_main.link(MAIN_GOTO_GACHA, destination=page_gacha)

# Sanctuary
page_sanctuary = Page(SANCTUARY_CHECK)
page_sanctuary.link(MENU, destination=page_menu)
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

# Store
page_store = Page(STORE_CHECK)
page_store.link(MENU, destination=page_menu)
page_main.link(MAIN_GOTO_STORE, destination=page_store)

# Mission reward popup with daily / weekly tabs
page_mission_reward_daily = Page(DAILY_TAB_CHECK)
page_mission_reward_daily.link(AD_BUFF_X_CLOSE, destination=page_main)

page_mission_reward_weekly = Page(WEEKLY_TAB_CHECK)
page_mission_reward_weekly.link(AD_BUFF_X_CLOSE, destination=page_main)

page_mission_reward = Page(MISSION_REWARD_CHECK)
page_mission_reward.link(AD_BUFF_X_CLOSE, destination=page_main)
page_mission_reward.link(DAILY_TAB_ENTRY, destination=page_mission_reward_daily)
page_mission_reward.link(WEEKLY_TAB_ENTRY, destination=page_mission_reward_weekly)
page_mission_reward_daily.link(WEEKLY_TAB_ENTRY, destination=page_mission_reward_weekly)
page_mission_reward_weekly.link(DAILY_TAB_ENTRY, destination=page_mission_reward_daily)
page_menu.link(MENU_GOTO_MISSION_REWARD, destination=page_mission_reward)
page_menu.link(MENU_GOTO_MISSION_REWARD, destination=page_mission_reward_daily)

# Arena mode-selection popup
page_arena_mode_popup = Page(ARENA_COMMON_ENTRY)
page_arena_mode_popup.link(AD_BUFF_X_CLOSE, destination=page_main)
page_main.link(ARENA_ENTRY, destination=page_arena_mode_popup)

# Arena
page_arena = Page(ARENA_CHECK)
page_arena.link(BACK, destination=page_main)
page_arena_mode_popup.link(ARENA_COMMON_ENTRY, destination=page_arena)

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

# Combat stage selection page (element / grade)
page_combat_stage = Page((ALTER_CHECK, HUNT_CHECK))
page_combat_stage.link(BACK, destination=page_combat_common)

# Combat prepare page
page_combat_prepare = Page(REPEAT_COMBAT_MENU)
page_combat_prepare.link(BACK, destination=page_combat_stage)

# Pets
page_pets = Page(PETS_CHECK)
page_pets.link(MENU, destination=page_menu)
page_menu.link(MENU_GOTO_PETS, destination=page_pets)

# Knights
page_knights = Page(KNIGHTS_CHECK)
page_knights.link(MENU, destination=page_menu)
page_menu.link(MENU_GOTO_KNIGHTS, destination=page_knights)

# Knights sub pages
# Donate page
page_knights_donate = Page(DONATE_CHECK)
page_knights_donate.link(BACK, destination=page_main)
page_knights.link(DONATE, destination=page_knights_donate)

# Support page
page_knights_support = Page(SUPPORT_CHECK)
page_knights_support.link(BACK, destination=page_main)
page_knights.link(SUPPORT, destination=page_knights_support)

# Expedition panel
page_knights_expedition = Page(TEAM_BATTLE)
page_knights_expedition.link(BACK, destination=page_main)
page_knights.link(EXPEDITION, destination=page_knights_expedition)

# Weekly task page
page_knights_weekly_task = Page(WEEKLY_TASK_CHECK)
page_knights_weekly_task.link(BACK, destination=page_main)
page_knights.link(WEEKLY_TASK, destination=page_knights_weekly_task)

# Knights first-level tabs are mutually reachable.
page_knights_donate.link(SUPPORT, destination=page_knights_support)
page_knights_donate.link(EXPEDITION, destination=page_knights_expedition)
page_knights_donate.link(WEEKLY_TASK, destination=page_knights_weekly_task)
page_knights_support.link(DONATE, destination=page_knights_donate)
page_knights_support.link(EXPEDITION, destination=page_knights_expedition)
page_knights_support.link(WEEKLY_TASK, destination=page_knights_weekly_task)
page_knights_expedition.link(DONATE, destination=page_knights_donate)
page_knights_expedition.link(SUPPORT, destination=page_knights_support)
page_knights_expedition.link(WEEKLY_TASK, destination=page_knights_weekly_task)
page_knights_weekly_task.link(DONATE, destination=page_knights_donate)
page_knights_weekly_task.link(SUPPORT, destination=page_knights_support)
page_knights_weekly_task.link(EXPEDITION, destination=page_knights_expedition)

# Team battle home page
page_knights_team_battle = Page(KNIGHTS_CREST)
page_knights_team_battle.link(BACK, destination=page_knights)
page_knights_expedition.link(TEAM_BATTLE, destination=page_knights_team_battle)

# Team battle truce page (no crest during waiting period)
page_knights_team_battle_waiting = Page(WAITING_FOR_WAR)
page_knights_team_battle_waiting.link(BACK, destination=page_main)
page_knights_expedition.link(TEAM_BATTLE, destination=page_knights_team_battle_waiting)

# World boss home page
page_knights_world_boss = Page(READY_TO_FIGHT)
page_knights_world_boss.link(BACK, destination=page_knights)
page_knights_expedition.link(WORLD_BOSS, destination=page_knights_world_boss)

# Knights v2
page_knights_v2 = Page(KNIGHTS_V2_CHECK)
page_knights_v2.link(MENU, destination=page_menu)
page_menu.link(MENU_GOTO_KNIGHTS, destination=page_knights_v2)

page_knights_v2_support = Page(KNIGHTS_V2_SUPPORT_CHECK)
page_knights_v2_support.link(AD_BUFF_X_CLOSE, destination=page_knights_v2)
page_knights_v2.link(KNIGHTS_ACTIVITY_ENTRY, destination=page_knights_v2_support)

page_knights_v2_weekly_task = Page(KNIGHTS_V2_WEEKLY_TASK_CHECK)
page_knights_v2_weekly_task.link(AD_BUFF_X_CLOSE, destination=page_knights_v2)
page_knights_v2_support.link(KNIGHTS_V2_WEEKLY_TASK_ENTRY, destination=page_knights_v2_weekly_task)

page_knights_v2_world_boss = Page(WORLD_BOSS_V2_CHECK)
page_knights_v2_world_boss.link(BACK, destination=page_knights_v2)
page_knights_v2.link(WORLD_BOSS_ENTRY, destination=page_knights_v2_world_boss)

page_knights_v2_team_battle = Page(KNIGHTS_V2_CREST)
page_knights_v2_team_battle.link(BACK, destination=page_knights_v2)

# # Battle Pass
# page_battle_pass = Page(BATTLE_PASS_CHECK)
# page_battle_pass.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_BATTLE_PASS, destination=page_battle_pass)
#
# # Event
# page_event = Page(EVENT_CHECK)
# page_event.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_EVENT, destination=page_event)
#
# # Map
# page_map = Page(MAP_CHECK)
# page_map.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_MAP, destination=page_map)
#
# # page_world, subpage of map, used to choose a world/planet e.g. Herta Space Station
# page_world = Page(WORLD_CHECK)
# page_world.link(BACK, destination=page_map)
# page_map.link(MAP_GOTO_WORLD, destination=page_world)
#
# # Tutorial
# page_tutorial = Page(TUTORIAL_CHECK)
# page_tutorial.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_TUTORIAL, destination=page_tutorial)
#
# # Mission
# page_mission = Page(MISSION_CHECK)
# page_mission.link(CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_MISSION, destination=page_mission)
#
# # Message
# page_message = Page(MESSAGE_CLOSE)
# page_message.link(MESSAGE_CLOSE, destination=page_main)
# page_main.link(MAIN_GOTO_MESSAGE, destination=page_message)
#
# # Camera
# page_camera = Page(CAMERA_CHECK)
# page_camera.link(CLOSE, destination=page_menu)
# page_menu.link(MENU_GOTO_CAMERA, destination=page_camera)
#
# # Synthesize
# page_synthesize = Page(SYNTHESIZE_CHECK)
# page_synthesize.link(CLOSE, destination=page_menu)
# page_menu.link(MENU_GOTO_SYNTHESIZE, destination=page_synthesize)
#
# # Assignment
# page_assignment = Page(ASSIGNMENT_CHECK)
# page_assignment.link(CLOSE, destination=page_main)
# page_menu.link(MENU_GOTO_ASSIGNMENT, destination=page_assignment)
#
# # Forgotten Hall
# page_forgotten_hall = Page(FORGOTTEN_HALL_CHECK)
# page_forgotten_hall.link(CLOSE, destination=page_main)
#
# # Rogue, Simulated Universe
# page_rogue = Page(ROGUE_CHECK)
# page_rogue.link(CLOSE, destination=page_main)
#
# # Planner result
# page_planner = Page(PLANNER_CHECK)
# page_planner.link(CLOSE, destination=page_menu)
