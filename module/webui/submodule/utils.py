def get_available_func():
    return (
        'Benchmark',
        'CombatFarm',
        'EquipmentReroll',
    )


def get_tool_runner(func):
    if func == 'Brawl':
        from tasks.brawl.tool import run_tool
        return run_tool
    if func == 'BuyPenguins':
        from tasks.sanctuary.buy_penguins_tool import run_tool
        return run_tool
    if func == 'CommunityAuth':
        from tasks.community_auth.community_auth import run_tool
        return run_tool
    if func == 'DimensionalExploration':
        from tasks.dimensional_exploration.tool import run_tool
        return run_tool
    return None
