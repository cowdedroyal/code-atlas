from code_atlas.cluster import draft_regions


def test_leiden_splits_dependency_communities_inside_a_directory_deterministically():
    files = [{"path": f"app/f{i}.py", "kind": "source"} for i in range(12)]
    weights = {(a["path"], b["path"]): 1 for i,a in enumerate(files) for j,b in enumerate(files)
               if i != j and i // 6 == j // 6}
    regions, assign = draft_regions(files, "repo", weights)
    assert assign["app/f0.py"] == assign["app/f5.py"]
    assert assign["app/f0.py"] != assign["app/f6.py"]
    assert assign == draft_regions(files, "repo", weights)[1]
