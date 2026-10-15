from novelvideo.media_capabilities.video.h3_wire import inspect_h3_reference_semantics


def test_inline_definitions_report_actionable_newline_error():
    definitions = '<Subject 1>: Lia <Picture 1>; <Subject 2>: Noel <Picture 2>'
    retention = [('<Subject 1>', 'fully_preserved - identity'),
                 ('<Subject 2>', 'fully_preserved - identity')]
    action = '<Subject 1> speaks while <Subject 2> writes.'
    assert 'reference_definitions_need_newlines' in inspect_h3_reference_semantics(definitions, retention, action)
    assert inspect_h3_reference_semantics(definitions.replace('; ', '\n'), retention, action) == ()
