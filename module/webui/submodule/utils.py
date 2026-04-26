def get_available_func():
    return (
        'Benchmark',
    )


def get_tool_runner(func):
    if func == 'CommunityAuth':
        from tasks.community_auth.community_auth import run_tool
        return run_tool
    return None
