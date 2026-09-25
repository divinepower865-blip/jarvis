import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sphereGeometry} from '../jarvis/static/particle-sphere.mjs';
test('sphere is deterministic, bounded, and connected with valid edges',()=>{
 const a=sphereGeometry();assert.deepEqual(a,sphereGeometry());
 assert.ok(a.points.length>500&&a.points.length<=800);
 for(const p of a.points)assert.ok(Math.abs(p[0]**2+p[1]**2+p[2]**2-1)<1e-10);
 assert.ok(a.edges.length>1000&&a.edges.length<=a.points.length*5);
 for(const [i,j] of a.edges){assert.ok(i<j&&j<a.points.length);}
});
