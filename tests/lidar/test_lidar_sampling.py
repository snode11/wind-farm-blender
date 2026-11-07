from wfrl.lidar.sampling import compare_grids


def test_new_grid_miss_changes_are_visible():
    def data(values):
        return dict(motion=[{'time_s':0},{'time_s':1}], measurements=[dict(time_s=i/10,expected=True,passage_id='p1',beams={'B2':dict(valid=v,error_m=.1 if v else None)}) for i,v in enumerate(values)])
    result=compare_grids(data([False,False]),data([False,True,False,False]),10,20)
    assert result['base']['valid_ratio']==0
    assert result['refined']['valid_ratio']==.25
    assert result['refined_minus_base']['missed_passage_count']==-1
    assert result['passage_comparison']['p1']['validity_class_changed']
    assert result['verdict']=='NUMERICAL_COMPARISON_ONLY'
