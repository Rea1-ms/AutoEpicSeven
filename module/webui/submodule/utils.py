def get_available_func():
    return (
        'Benchmark',
        'CombatFarm',
    )


def get_tool_runner(func):
    if func == 'CommunityAuth':
        from tasks.community_auth.community_auth import run_tool
        return run_tool
    if func == 'DimensionalExploration':
        from tasks.dimensional_exploration.tool import run_tool
        return run_tool
    return None
