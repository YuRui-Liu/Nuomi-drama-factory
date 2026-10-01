from novelvideo.task_backend.runners.narrative_group import _grid_prompt

def test_diptych_declares_row_column_orientation():
    prompt = _grid_prompt({'layout':{'rows':1,'columns':2},'aspect_ratio':'16:9',
                          'beats':[{'action':'A'},{'action':'B'}]})
    assert '1 row(s) and 2 column(s)' in prompt
    assert 'side by side' in prompt
    assert 'never stack' in prompt
