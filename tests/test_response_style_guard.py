from src.services.response_style_guard import ResponseStyleGuard


def test_filter_repeated_input_prefix_strips_streamed_echo():
    guard = ResponseStyleGuard()
    user_input = "\u4eca\u5929\u5fc3\u91cc\u6709\u70b9\u95f7\uff0c\u60f3\u804a\u804a\u5929"

    first, pending, done = guard.filter_repeated_input_prefix(user_input[:5], user_input)
    assert first == ""
    assert pending == user_input[:5]
    assert done is False

    second, pending, done = guard.filter_repeated_input_prefix(
        pending + user_input[5:] + "...\u5c0f\u6696\u5728\uff0c\u60a8\u6162\u6162\u8bf4\u3002",
        user_input,
    )

    assert second == "\u5c0f\u6696\u5728\uff0c\u60a8\u6162\u6162\u8bf4\u3002"
    assert pending == ""
    assert done is True


def test_filter_repeated_input_prefix_keeps_normal_reply_start():
    guard = ResponseStyleGuard()
    user_input = "\u4eca\u5929\u5fc3\u91cc\u6709\u70b9\u95f7\uff0c\u60f3\u804a\u804a\u5929"
    reply = "\u6211\u542c\u89c1\u60a8\u8fd9\u4f1a\u513f\u5fc3\u91cc\u6709\u70b9\u95f7\uff0c\u6211\u5728\u8fd9\u513f\u966a\u60a8\u3002"

    emitted, pending, done = guard.filter_repeated_input_prefix(reply, user_input)

    assert emitted == reply
    assert pending == ""
    assert done is True
